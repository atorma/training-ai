UV ?= uv

.PHONY: bootstrap lint lint-fix build run-web run-cli test

bootstrap:
	$(UV) sync --all-extras --locked
	$(UV) run --locked prek install

lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
	$(UV) run --locked prek run markdownlint-check --stage manual --all-files

lint-fix:
	$(UV) run --locked prek run --all-files

# Packaging is optional for local development; no frontend build exists yet.
build:
	$(UV) build

run-web:
	$(UV) run --locked ./run-web.sh --reload

run-cli:
	$(UV) run --locked training-ai

# Browser/Playwright tests will have a separate target when introduced.
test:
	$(UV) run --locked pytest
