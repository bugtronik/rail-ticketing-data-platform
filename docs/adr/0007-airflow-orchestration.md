# ADR 0007 — Orchestration: Airflow 3 calls the project's command-line tools

- **Status:** Accepted
- **Date:** 2026-10-26

## Context

Extraction (ADR 0005) and dbt (ADR 0006) work when run by hand. They must
now run on their own, every hour, with retries and a visible history, and
the whole platform must start with one command on a 16 GB laptop.

## Decision

**One DAG, three tasks, each a command already used by hand.**
`extract` (`rail-extract run`) → `check_freshness` (`dbt source freshness`)
→ `dbt_build` (`dbt build`), every hour. Airflow decides when to run them,
retries them and keeps their logs; it holds no business logic. The same
commands run from `make` on a laptop and from Airflow in Docker, so a
problem can always be reproduced outside Airflow.

**Idempotent by construction, not by date.** Extraction resumes from its
watermark and dbt rebuilds from `raw`. A retry, a manual re-run or a run
after a long stop all converge to the same state. No task uses the run's
logical date: the pipeline catches up to "now" instead of processing an
hour slice, so `catchup=False` and `max_active_runs=1`.

**Retries for transient failures only.** `extract` retries 3 times with
exponential backoff (1, 2, 4 min), which covers a database restart. Checked
by stopping MariaDB during a run: attempt 1 failed, attempt 2 succeeded once
MariaDB was back. `check_freshness` never retries (stale data is not
transient); `dbt_build` retries once.

**Airflow 3.3, LocalExecutor, no Celery.** Tasks run as processes of the
scheduler. CeleryExecutor (Redis + workers) scales out to several machines;
here there is one machine and three tasks per hour. Components: API server
(UI), scheduler, DAG processor, plus their own PostgreSQL database, separate
from the warehouse. No triggerer: no task uses deferrable operators.

**One image, two virtualenvs.** The image extends `apache/airflow:3.3.2`
and installs the project and dbt with `uv sync --locked` in
`/opt/pipeline/.venv`. Airflow's constraints pin numpy 2.5.3 and psycopg
3.3.5; `uv.lock` pins numpy ≥ 2.5.4 and psycopg ≥ 3.3.6: one environment
cannot satisfy both. Tasks call the tools by their full path, so the two
environments never mix. The code, the dbt project and the DAG are copied
into the image (not mounted): what runs is exactly what was built.

**`docker compose up` brings everything up, in order.** MariaDB → `seed`
(90 days of history if the source is empty, otherwise nothing:
`--if-empty`) → `generator` (live activity) and Airflow's scheduler. The
DAG is not paused at creation, so the first run starts without a click.
Each service receives only the passwords it needs: the generator gets the
application account only, the scheduler the extraction and dbt accounts;
the API server and the DAG processor get no database password of the
pipeline at all.

## Consequences

- **+** The pipeline runs, retries and reports on its own; its history is
  in the UI, task by task, with the dbt and extractor logs.
- **+** The tasks stay testable without Airflow (`make extract`,
  `make transform`), and the DAG itself is checked in CI (import errors).
- **−** Airflow needs several GB of RAM (its documentation asks for 4 GB);
  the whole stack is a lot for a pipeline of three commands. It is the
  standard tool, and the point of this project is to run it properly.
- **−** The marts are rebuilt in full every hour (~1 min on 2 years of
  data). Incremental models are planned (J7).
- **−** Changing a model or the DAG means rebuilding the image
  (`make up` does it; the dependency layer is cached and rebuilt only when
  `uv.lock` changes).
- **−** Alerts (e-mail, chat) on failure are not configured: failures are
  visible in the UI only.
