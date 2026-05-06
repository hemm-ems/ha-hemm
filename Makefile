.PHONY: test test-container test-pi test-slow ci ci-full lint format

## Default: fast unit tests only
test:
	uv run pytest

## Container-based integration tests (Docker required)
test-container:
	uv run pytest -m container

## Pi hardware tests (manual / self-hosted runner)
test-pi:
	uv run pytest -m pi

## Long-running simulation tests
test-slow:
	uv run pytest -m slow

## CI minimum: lint + unit tests
ci: lint test

## CI full: ci + container tests
ci-full: ci test-container

## Lint and format check
lint:
	uv run ruff check custom_components/ tests/
	uv run ruff format --check custom_components/ tests/

## Auto-format
format:
	uv run ruff format custom_components/ tests/
	uv run ruff check --fix custom_components/ tests/

## Build (HACS compatible zip)
build:
	@echo "Build step: package custom_components/hemm for HACS"
	@mkdir -p dist
	@cd custom_components && zip -r ../dist/hemm.zip hemm/
