# Repository Guidelines

## Project Structure & Module Organization

- Source code should live in `src/` (flat modules like `src/cli.py`).
- Tests should live in `tests/` and mirror the source tree (e.g., `tests/utils/test_dates.py` for `src/utils/dates.py`).
- Supporting materials belong in `docs/`, `scripts/`, and `assets/`.
- Keep modules small and single-purpose; prefer explicit entry points like `src/cli.py` or `src/main.py`.

## Build, Test, and Development Commands

- `uv venv` - create a local virtual environment. Needs to be done once after cloning the repo.
- `source .venv/bin/activate` - activate the environment.
- `uv sync --all-extras` - install runtime + dev dependencies from `pyproject.toml`.
- `uv run training-ai` - run the CLI chat assistant.
- `uv run python -m cli` - run the CLI from the repo without installing.
- `pytest` - run the full test suite.

## Coding Style & Naming Conventions

- Indentation: 4 spaces for Python.
- Filenames: `kebab-case` for docs/scripts, `snake_case` for Python modules, `PascalCase` for class names.
- Follow PEP 8 and keep lines under 100 characters unless a formatter enforces otherwise.
- If a formatter/linter is added (e.g., Ruff), keep this section aligned with it.

## Testing Guidelines

- Add tests for all new functionality and bug fixes.
- Default naming: `tests/**/test_*.py`.
- Prefer fast, deterministic tests; keep integration tests clearly labeled.

## Security & Configuration

- Never commit secrets. Use `.env` for local overrides and provide `.env.example` for required keys.
