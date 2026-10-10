# ADR 0002 — Python tooling: uv, Ruff, SQLFluff, pre-commit

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

The project contains Python (data generator, extractor, Airflow DAGs) and SQL
(MariaDB DDL, warehouse migrations, dbt models). It must be reproducible on
another machine and checked automatically on every push.

## Decision

- **uv** manages the Python version (3.12, pinned in `.python-version`) and
  dependencies, with a committed lock file (`uv.lock`). CI installs with
  `uv sync --locked`, which fails if the lock file is stale.
- **Ruff** lints and formats Python. It replaces flake8 + isort + black with
  one tool. The `S` (bandit) rules flag hardcoded passwords.
- **SQLFluff** lints SQL; it understands both PostgreSQL and MariaDB dialects
  and, later, dbt's Jinja templating.
- **pre-commit** runs all checks on `git commit` and in CI with the same
  command (`make lint`). Ruff and SQLFluff hooks call `uv run`, so their
  versions come from `uv.lock` only.

## Consequences

- **+** One source of truth for tool versions and for "what clean means":
  if it passes locally, it passes in CI.
- **+** Problems are caught before the commit, not in code review.
- **−** Contributors must install uv (one command).
- **−** The Airflow container image manages its own Python dependencies;
  they will be pinned separately (Airflow constraint files).
