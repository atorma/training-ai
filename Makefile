UV ?= uv
NPM ?= npm

.PHONY: bootstrap lint lint-fix build build-web run-web run-cli test test-e2e install-browser

bootstrap:
	$(UV) sync --all-extras --locked
	$(UV) run --locked prek install
	$(NPM) ci
	$(MAKE) install-browser

lint:
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
	$(UV) run --locked prek run markdownlint-check --stage manual --all-files
	$(NPM) run lint
	$(NPM) exec tsc -- --noEmit -p tsconfig.json

lint-fix:
	$(NPM) run lint-fix
	$(UV) run --locked prek run --all-files

build: build-web
	$(UV) build

build-web:
	$(NPM) run build

run-web: build-web
	$(UV) run --locked uvicorn web:app --host 0.0.0.0 --port 7932 --reload --timeout-graceful-shutdown 5

run-cli:
	$(UV) run --locked training-ai

# Browser/Playwright checks are separate from the fast Python suite.
test:
	$(UV) run --locked pytest

test-e2e: build-web
	$(UV) run --locked --all-extras $(NPM) run test-e2e

install-browser:
	$(NPM) exec playwright install chromium
