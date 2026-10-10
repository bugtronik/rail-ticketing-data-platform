# Developer shortcuts. Run `make help` to list them.
# Every target is a thin wrapper: the real commands stay readable below.

.DEFAULT_GOAL := help
.PHONY: help install lint format

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
