#!/usr/bin/env bash
# Waits for the FIRST run of the pipeline DAG, started on its own by the
# scheduler after `docker compose up`, and fails if that run fails.
#
# The state is read from Airflow's own database (table dag_run): one psql
# query, no credentials to handle, and the same answer the UI shows.
# Usage: wait_for_first_dag_run.sh [timeout_seconds]
set -euo pipefail

timeout="${1:-900}"
dag_id="rail_ticketing_pipeline"
query="SELECT state FROM dag_run WHERE dag_id = '${dag_id}' ORDER BY id LIMIT 1"
deadline=$((SECONDS + timeout))

while ((SECONDS < deadline)); do
    state="$(docker compose exec -T airflow-db psql -U airflow -d airflow -tAc "${query}")"
    echo "$(date +%T) first run of ${dag_id}: ${state:-not created yet}"
    case "${state}" in
        success) exit 0 ;;
        failed) exit 1 ;;
    esac
    sleep 15
done

echo "Timed out after ${timeout} s" >&2
exit 1
