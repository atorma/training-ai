# Repository Guidelines

## Project Structure & Module Organization

- Source code should live in `src/` (flat modules like `src/cli.py`).
- Tests should live in `tests/` and mirror the source tree
  (e.g., `tests/utils/test_dates.py` for `src/utils/dates.py`).
- Supporting materials belong in `docs/`, `scripts/`, and `assets/`.
- Deployment scripts belong in `deployment` and Kubernetes manifests in `deployment/k8s/`.
- Keep modules small and single-purpose; prefer explicit entry points like `src/cli.py` or `src/main.py`.
- Start with endpoint handlers calling concrete query/command objects that wrap database
  access and expose domain objects and mutation operations. Add service layers only when
  behavior warrants them; avoid speculative abstractions.
- All database schema changes and persistent data migrations must use the shared runner
  and append-only registry in `src/database_migrations.py`. Never edit applied migrations.
  Follow `docs/database-migrations.md` and add real-SQLite upgrade/data-preservation tests.

## Build, Test, and Development Commands

Use Make targets for development tasks.

- `make bootstrap` - install locked dependencies, Git hooks, and Playwright Chromium
  (requires Make, uv, Node.js, and npm).
- `make lint` - check Python and tracked Markdown without modifying source files.
- `make lint-fix` - apply formatting and supported lint fixes.
- `make hooks` - run pre-commit hooks on tracked and untracked, nonignored files.
- `make render-k8s` - render application manifests locally without accessing a cluster.
- `make render-k8s-storage` - render storage manifests locally without accessing a cluster.
- `make build` - create Python packages in `dist/`; optional for local development.
- `make build-web` - compile the React frontend into Python's static assets.
- `make build-docker` - build web assets locally, then build the Docker image.
- `make run-web` - build the UI once and serve it on port 7932 as production does, with reload.
- `make dev-web` - fastest UI loop: Vite hot reload on port 5173 proxying the API on port 7932.
- `make run-cli` - start the interactive CLI agent.
- `make test` - run fast pytest tests; browser/Playwright tests are separate.
- `make install-browser` - reinstall Chromium separately; bootstrap includes installation.
- `make test-e2e` - test browser flows against the real local API and temporary SQLite.

## Coding Style & Naming Conventions

- Indentation: 4 spaces for Python.
- Filenames: `kebab-case` for docs/scripts, `snake_case` for Python modules,
  `PascalCase` for class names.
- Ruff formats Python with a 100-character line length and Python 3.12 target.
- Ruff checks core errors, unused code/imports, import sorting, and Bugbear rules.
- prek runs Ruff safe fixes followed by formatting on changed Python files at commit time.
- markdownlint-cli2 checks and fixes changed Markdown files. Prose has a 100-character
  line limit; code blocks and tables are exempt.
- Python, TypeScript, and Markdown must have lint checks run through pre-commit hooks.
  Python, TypeScript, and Markdown hooks are configured. Install hooks
  once per clone using `make bootstrap`.

## Testing Guidelines

- Add tests for all new functionality and bug fixes.
- Default naming: `tests/**/test_*.py`.
- Prefer fast, deterministic tests; keep integration tests clearly labeled.
- Prefer API tests exercising the real application and a real temporary SQLite database.
  Avoid unit tests built around mocked internal collaborators or database operations.
- Cover important happy paths with browser-level end-to-end tests against the real API
  and database. Cover edge cases primarily at the API level or deeper where necessary.
- Add focused tests for important services only when their behavior merits separate coverage.
- Keep architecture shallow; do not add layers merely to make them mockable.

## Security & Configuration

- Never commit secrets. Use `.env` for local overrides and provide `.env.example` for required keys.

## Personal Agent Context

This project uses `.agent/` as the source of truth for agent context.

At the start of work, read:

1. `.agent/rules/`
2. `.agent/purpose.md`
3. `.agent/memory.md`
4. last 5–10 entries in `.agent/session-log.md`
5. relevant `.agent/docs/`

Before marking work complete, update `.agent/memory.md` and append
to `.agent/session-log.md`.
