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

## Build, Test, and Development Commands

- `uv venv` - create a local virtual environment. Needs to be done once after cloning the repo.
- `source .venv/bin/activate` - activate the environment.
- `uv sync --all-extras` - install runtime + dev dependencies from `pyproject.toml`.
- `uv run training-ai` - run the CLI chat assistant.
- `uv run python -m cli` - run the CLI from the repo without installing.
- `pytest` - run the full test suite.
- `uv run prek install` - install the Git pre-commit hook once per clone.
- `uv run prek run --all-files` - run all configured hooks.
- `uv run ruff check .` and `uv run ruff format --check .` - check Python without edits.

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
  Python and Markdown hooks are configured; TypeScript hooks are pending. Install hooks
  once per clone using the command above.

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
