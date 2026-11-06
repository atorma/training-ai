UV ?= uv
NPM ?= npm

.DEFAULT_GOAL := help

.PHONY: help bootstrap install-browser
.PHONY: lint lint-fix hooks
.PHONY: build build-web build-docker
.PHONY: run-web dev-web run-mcp
.PHONY: test test-e2e test-integration eval-memory
.PHONY: render-k8s render-k8s-storage render-k8s-mcp build-and-deploy-k8s deploy-k8s-mcp

bootstrap: ## Install locked dependencies, Git hooks and Playwright Chromium
	$(UV) sync --all-extras --locked
	$(UV) run --locked prek install
	$(NPM) ci
	$(MAKE) install-browser

lint: ## Check Python, TypeScript and Markdown without modifying files
	$(UV) run --locked ruff check .
	$(UV) run --locked ruff format --check .
	$(UV) run --locked mypy src/training_ai
	$(UV) run --locked pyright src/training_ai
	$(UV) run --locked prek run markdownlint-check --stage manual --all-files
	$(NPM) run lint
	$(NPM) exec tsc -- --noEmit -p tsconfig.json

lint-fix: ## Apply formatting and supported lint fixes
	$(NPM) run lint-fix
	$(UV) run --locked prek run --all-files

# Include new, unstaged files as well as tracked files; ignored files stay excluded.
hooks: ## Run pre-commit hooks on tracked and untracked, nonignored files
	git ls-files --cached --others --exclude-standard -z | xargs -0 $(UV) run --locked prek run --files

build: build-web ## Create Python packages in dist/
	$(UV) build

build-web: ## Compile the React frontend into static assets
	$(NPM) run build

build-docker: ## Build web assets, then the Docker image
	sh deployment/build.sh

WEB_PORT ?= 7932
UVICORN_DEV = $(UV) run --locked uvicorn training_ai.web:app --host 0.0.0.0 --port $(WEB_PORT) --reload \
	--timeout-graceful-shutdown 5

# Serves the built bundle as production does; use dev-web for frontend work.
run-web: build-web ## Build the UI once and serve it on WEB_PORT, with reload
	$(UVICORN_DEV)

# Fastest UI loop: Vite hot reload on port 5173, proxying API calls to Uvicorn.
dev-web: ## Run Vite hot reload on port 5173, proxying the API on WEB_PORT
	@WEB_PORT=$(WEB_PORT) node_modules/.bin/vite --host 0.0.0.0 & vite=$$!; \
	trap 'kill $$vite 2>/dev/null' EXIT INT TERM; \
	$(UVICORN_DEV)

# Local pin, separate from deployment/k8s/intervals-mcp so new images can be tried first.
INTERVALS_MCP_IMAGE = ghcr.io/futureweb/intervals-mcp-server@sha256:93dad39e589142ed14c706759bbb143fca87636ae3a65ba71ed13c5626fbb31f
MCP_PORT ?= 8000

# Local Intervals.icu MCP server for MCP_SERVER_URL=http://127.0.0.1:8000/mcp.
# Credentials and TIMEZONE come from .env, parsed by python-dotenv as the app does, and stay out of argv.
run-mcp: ## Run the Intervals.icu MCP server locally on MCP_PORT
	@test -f .env || (echo "Copy .env.example to .env and set the INTERVALS_* credentials."; exit 1)
	$(UV) run --locked dotenv -f .env run -- sh -c '\
		API_KEY="$$INTERVALS_API_KEY" ATHLETE_ID="$$INTERVALS_ATHLETE_ID" ATHLETE_TIMEZONE="$$TIMEZONE" \
		exec docker run --rm --init -p 127.0.0.1:$(MCP_PORT):8000 -e API_KEY -e ATHLETE_ID -e ATHLETE_TIMEZONE \
		-e MCP_TRANSPORT=http -e FASTMCP_HOST=0.0.0.0 -e FASTMCP_PORT=8000 $(INTERVALS_MCP_IMAGE)'

# Browser/Playwright checks are separate from the fast Python suite.
test: ## Run fast pytest tests
	$(UV) run --locked pytest

test-e2e: build-web ## Run browser tests against the real local API and temporary SQLite
	$(UV) run --locked --all-extras $(NPM) run test-e2e

# Integration tests against a local Google Cloud Storage emulator in Docker; no cloud access.
GCS_EMULATOR_IMAGE = fsouza/fake-gcs-server:1.52.2
GCS_EMULATOR_PORT ?= 4443
test-integration: ## Run integration tests against a local GCS emulator in Docker
	@cid=$$(docker run -d --rm -p 127.0.0.1:$(GCS_EMULATOR_PORT):4443 $(GCS_EMULATOR_IMAGE) \
		-scheme http -public-host 127.0.0.1:$(GCS_EMULATOR_PORT)) || exit 1; \
	trap 'docker stop $$cid >/dev/null' EXIT; \
	for i in $$(seq 50); do curl -sf http://127.0.0.1:$(GCS_EMULATOR_PORT)/storage/v1/b >/dev/null && break; sleep 0.2; done; \
	STORAGE_EMULATOR_HOST=http://127.0.0.1:$(GCS_EMULATOR_PORT) $(UV) run --locked pytest -m integration

# Opt-in paid provider calls using fictional data and temporary SQLite; no external MCP.
eval-memory: ## Run the paid memory evaluation (requires EVAL_MODEL)
	@test -n "$(EVAL_MODEL)" || (echo "Set EVAL_MODEL to the provider model to evaluate."; exit 1)
	$(UV) run --locked python scripts/evaluate-memory.py --model "$(EVAL_MODEL)"

install-browser: ## Reinstall Playwright Chromium
	$(NPM) exec playwright install chromium

# Render locally without contacting a cluster or applying changes.
render-k8s: ## Render application manifests without contacting a cluster
	kubectl kustomize deployment/k8s/app

render-k8s-storage: ## Render storage manifests without contacting a cluster
	kubectl kustomize deployment/k8s/storage

render-k8s-mcp: ## Render Intervals MCP manifests without contacting a cluster
	kubectl kustomize deployment/k8s/intervals-mcp

build-and-deploy-k8s: build-docker ## Build, push and deploy the application
	sh deployment/push.sh
	sh deployment/deploy.sh

deploy-k8s-mcp: ## Deploy the Intervals MCP server
	sh deployment/deploy-intervals-mcp.sh

# Lists every target annotated with a trailing "## description" comment.
help: ## Show this help
	@awk -F ':.*## ' '/^[a-zA-Z0-9_-]+:.*## / { printf "%-22s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
