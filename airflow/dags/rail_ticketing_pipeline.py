"""Hourly pipeline: copy the new source changes to the warehouse, then rebuild the marts.

    extract  ->  check_freshness  ->  dbt_build

Each task runs one of the project's command-line tools, exactly as `make`
does on a laptop: Airflow only decides WHEN to run them, retries them, and
keeps a history of every run. The business logic stays in the project, where
it is tested without Airflow.

Why the tasks can be re-run at any time (idempotency):
- `rail-extract run` reads what changed since its watermark and upserts it.
  Running it twice copies nothing the second time.
- `dbt build` rebuilds the small models from raw, and the incremental ones
  (fct_tickets) re-process the tickets that changed, replacing their rows
  by key: same input, same result.
So a retry, a manual re-run or two overlapping schedules cannot duplicate or
lose data. This is also why no task uses the run's logical date: the
pipeline always catches up to "now", it does not process an hour slice.
"""

import os
from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, Param

# Where the image installs the project (see airflow/Dockerfile). An
# environment variable, so the DAG can also be tested outside the image.
PIPELINE_HOME = os.environ.get("PIPELINE_HOME", "/opt/pipeline")
BIN = f"{PIPELINE_HOME}/.venv/bin"
DBT = f"{BIN}/dbt --no-use-colors"
DBT_DIRS = "--project-dir transform --profiles-dir transform"

default_args = {
    "owner": "data-engineering",
    # Transient failures (a database restarting, a network blip) are retried
    # 3 times, waiting 1, then 2, then 4 minutes.
    "retries": 3,
    "retry_delay": timedelta(minutes=1),
    "retry_exponential_backoff": 2.0,
    "max_retry_delay": timedelta(minutes=10),
    # A stuck task fails instead of blocking the next runs.
    "execution_timeout": timedelta(minutes=30),
}

with DAG(
    dag_id="rail_ticketing_pipeline",
    description="MariaDB source -> raw (incremental) -> dbt marts, every hour",
    schedule="@hourly",
    start_date=datetime(2026, 10, 1),
    # No backfill of past hours: one run already brings everything up to date.
    catchup=False,
    # Never two runs at once: they would wait on each other's locks anyway.
    max_active_runs=1,
    # `docker compose up` must start the pipeline without a click in the UI.
    is_paused_upon_creation=False,
    default_args=default_args,
    # "Trigger DAG w/ config" in the UI shows this as a checkbox. Needed once
    # after changing an incremental model's columns, or to rebuild
    # everything from raw. Snapshots are never rebuilt: their history
    # exists nowhere else.
    params={
        "full_refresh": Param(
            False, type="boolean", description="Rebuild the incremental models from scratch"
        ),
    },
    tags=["rail-ticketing"],
    doc_md=__doc__,
) as dag:
    extract = BashOperator(
        task_id="extract",
        bash_command=f"{BIN}/rail-extract run",
        cwd=PIPELINE_HOME,
    )

    # Fails if the last successful extraction is too old (error_after in
    # transform/models/staging/_sources.yml): marts are not rebuilt on stale
    # data without anyone noticing.
    check_freshness = BashOperator(
        task_id="check_freshness",
        bash_command=f"{DBT} source freshness {DBT_DIRS}",
        cwd=PIPELINE_HOME,
        retries=0,
    )

    # Snapshots, models and all their tests. A failed test fails the task,
    # and the failure is visible in the UI with dbt's log.
    dbt_build = BashOperator(
        task_id="dbt_build",
        # Jinja, rendered by Airflow when the task runs.
        bash_command=f"{DBT} build {DBT_DIRS}"
        + "{{ ' --full-refresh' if params.full_refresh else '' }}",
        cwd=PIPELINE_HOME,
        retries=1,
    )

    extract >> check_freshness >> dbt_build
