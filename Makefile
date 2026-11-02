# Developer shortcuts. Run `make help` to list them.
# Every target is a thin wrapper: the real commands stay readable below.

.DEFAULT_GOAL := help
.PHONY: help env install lint format up down reset reset-warehouse migrate-warehouse history ps logs run run-full \
	test extract extract-status freshness transform transform-full docs

# dbt reads its connection settings from .env, like the other tools.
DBT := uv run --env-file .env dbt
DBT_DIRS := --project-dir transform --profiles-dir transform

help: ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

env: ## Create .env from .env.example with random passwords (never overwrites)
	python3 scripts/new_env.py

install: ## Create the virtualenv from uv.lock and install git hooks
	uv sync
	uv run pre-commit install

lint: ## Run every check exactly as the CI does
	uv run pre-commit run --all-files

format: ## Auto-fix Python and SQL formatting
	uv run ruff check --fix .
	uv run ruff format .
	uv run sqlfluff fix .

up: ## Build the image, start the whole platform, wait until it is healthy
	docker compose up -d --build --wait

down: ## Stop the stack (data is kept in Docker volumes)
	docker compose down

reset: ## Stop the stack AND delete its data: init scripts re-run on next `up`
	docker compose down -v

reset-warehouse: ## Recreate an empty warehouse (source kept, customer history LOST)
	docker compose rm -sf warehouse
	docker volume rm rail-ticketing_warehouse_data
	docker compose up -d --wait warehouse

test: ## Run the tests, with connection settings loaded from .env (needs `make up`)
	uv run --env-file .env pytest

migrate-warehouse: ## Apply warehouse/migrations/ to an existing warehouse (safe to repeat)
	for f in warehouse/migrations/*.sql; do \
		echo "$$f"; \
		docker compose exec -T warehouse sh -c 'psql -v ON_ERROR_STOP=1 -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"' < "$$f" || exit 1; \
	done

history: ## Delete ALL data and restart with 2 years of history (~5-10 min)
	docker compose down -v
	SEED_DAYS=730 docker compose up -d --build --wait

ps: ## Show the state of every service
	docker compose ps -a

logs: ## Follow the logs of the generator and the scheduler (Ctrl+C to stop)
	docker compose logs -f --tail=20 generator airflow-scheduler

run: ## Trigger the pipeline now instead of waiting for the next hour
	docker compose exec airflow-scheduler airflow dags trigger rail_ticketing_pipeline

run-full: ## Trigger the pipeline now, rebuilding the incremental models from scratch
	docker compose exec airflow-scheduler airflow dags trigger rail_ticketing_pipeline \
		--conf '{"full_refresh": true}'

extract: ## Copy the source changes since the last run into the warehouse raw layer
	uv run --env-file .env rail-extract run

extract-status: ## Show, per table, the last change seen and the age of the raw copy
	uv run --env-file .env rail-extract status

freshness: ## Check that the last successful extraction is recent enough
	$(DBT) source freshness $(DBT_DIRS)

transform: ## Build and test the dbt models: staging -> intermediate -> marts
	$(DBT) build $(DBT_DIRS)

transform-full: ## Rebuild the incremental models from scratch (snapshots are kept)
	$(DBT) build $(DBT_DIRS) --full-refresh

docs: ## Generate the dbt documentation and serve it on http://localhost:8081
	$(DBT) docs generate $(DBT_DIRS)
	$(DBT) docs serve $(DBT_DIRS) --port 8081
