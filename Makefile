# Developer shortcuts. Run `make help` to list them.
# Every target is a thin wrapper: the real commands stay readable below.

.DEFAULT_GOAL := help
.PHONY: help install lint format up down reset test seed live extract extract-status

help: ## List available commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Create the virtualenv from uv.lock and install git hooks
	uv sync
	uv run pre-commit install

lint: ## Run every check exactly as the CI does
	uv run pre-commit run --all-files

format: ## Auto-fix Python and SQL formatting
	uv run ruff check --fix .
	uv run ruff format .
	uv run sqlfluff fix .

up: ## Start the stack in the background and wait until it is healthy
	docker compose up -d --wait

down: ## Stop the stack (data is kept in Docker volumes)
	docker compose down

reset: ## Stop the stack AND delete its data: init scripts re-run on next `up`
	docker compose down -v

test: ## Run the tests, with connection settings loaded from .env (needs `make up`)
	uv run --env-file .env pytest

seed: ## Fill the (empty) source with 2 years of history (needs `make up`)
	uv run --env-file .env rail-generator history

live: ## Simulate the production application in real time (Ctrl+C to stop)
	uv run --env-file .env rail-generator live

extract: ## Copy the source changes since the last run into the warehouse raw layer
	uv run --env-file .env rail-extract run

extract-status: ## Show, per table, the last change seen and the age of the raw copy
	uv run --env-file .env rail-extract status
