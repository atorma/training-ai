UV ?= uv
NPM ?= npm

.PHONY: bootstrap install-browser
.PHONY: lint lint-fix hooks
.PHONY: build build-web build-docker
.PHONY: run-web dev-web
.PHONY: test test-e2e eval-memory
.PHONY: render-k8s render-k8s-storage build-and-deploy-k8s

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

WEB_PORT ?= 7932
UVICORN_DEV = $(UV) run --locked uvicorn training_ai.web:app --host 0.0.0.0 --port $(WEB_PORT) --reload \
	--timeout-graceful-shutdown 5

# Serves the built bundle as production does; use dev-web for frontend work.
run-web: build-web
	$(UVICORN_DEV)

# Fastest UI loop: Vite hot reload on port 5173, proxying API calls to Uvicorn.
dev-web:
	@WEB_PORT=$(WEB_PORT) node_modules/.bin/vite --host 0.0.0.0 & vite=$$!; \
	trap 'kill $$vite 2>/dev/null' EXIT INT TERM; \
	$(UVICORN_DEV)

# Browser/Playwright checks are separate from the fast Python suite.
test:
	$(UV) run --locked pytest

test-e2e: build-web
	$(UV) run --locked --all-extras $(NPM) run test-e2e

# Opt-in paid provider calls using fictional data and temporary SQLite; no external MCP.
eval-memory:
	@test -n "$(EVAL_MODEL)" || (echo "Set EVAL_MODEL to the provider model to evaluate."; exit 1)
	$(UV) run --locked python scripts/evaluate-memory.py --model "$(EVAL_MODEL)"

install-browser:
	$(NPM) exec playwright install chromium

# Render locally without contacting a cluster or applying changes.
render-k8s:
	kubectl kustomize deployment/k8s/app

render-k8s-storage:
	kubectl kustomize deployment/k8s/storage

build-and-deploy-k8s: build-docker
	sh deployment/push.sh
	sh deployment/deploy.sh