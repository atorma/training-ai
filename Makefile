UV ?= uv
NPM ?= npm

.PHONY: bootstrap lint lint-fix hooks build build-web build-docker run-web run-cli test test-e2e install-browser render-k8s render-k8s-storage

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

# Include new, unstaged files as well as tracked files; ignored files stay excluded.
hooks:
	git ls-files --cached --others --exclude-standard -z | xargs -0 $(UV) run --locked prek run --files

build: build-web
	$(UV) build

build-web:
	$(NPM) run build

build-docker:
	sh deployment/build.sh

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

# Render locally without contacting a cluster or applying changes.
render-k8s:
	kubectl kustomize deployment/k8s/app

render-k8s-storage:
	kubectl kustomize deployment/k8s/storage
