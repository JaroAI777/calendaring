.PHONY: help dev lint format test changelog-draft clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-15s\033[0m %s\n", $$1, $$2}'

dev:  ## Install with dev dependencies (editable)
	@if [ "$$(id -u)" = "0" ]; then echo "Refusing to run make dev as root"; exit 1; fi
	@if ! python -c 'import sys; sys.exit(sys.prefix == sys.base_prefix)'; then \
		echo "Not in a virtualenv.  Create and activate one first:"; \
		echo "  python3 -m venv .venv && . .venv/bin/activate && make dev"; \
		exit 1; \
	fi
	python -m pip install -e ".[dev]"

lint:  ## Run ruff linter and formatter check, and mypy
	python -m ruff check src/ tests/
	python -m ruff format --check src/ tests/
	python -m mypy

format:  ## Auto-format code
	python -m ruff check --fix src/ tests/
	python -m ruff format src/ tests/

test:  ## Run tests
	python -m pytest

changelog-draft:  ## Show the change log the next release would get
	python -m towncrier build --draft --version next

clean:  ## Remove build artifacts
	rm -rf dist/ build/ *.egg-info src/*.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
