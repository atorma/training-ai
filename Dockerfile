# syntax=docker/dockerfile:1
FROM python:3.12-slim AS dependencies

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./
ARG TARGETARCH
# This layer changes only when dependency metadata or the lockfile changes.
RUN --mount=type=cache,id=training-ai-uv-${TARGETARCH},target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM dependencies AS builder

COPY README.md ./
COPY src ./src
# Built locally by make build-web before invoking Docker.
COPY src/web_assets/static ./src/web_assets/static

RUN --mount=type=cache,id=training-ai-uv-${TARGETARCH},target=/root/.cache/uv \
    test -s src/web_assets/static/index.html \
    && uv sync --locked --no-dev --no-editable \
    && uv pip check --python /opt/venv/bin/python

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd -g 1000 appgroup \
    && useradd -u 1000 -g 1000 -m -s /sbin/nologin appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

RUN mkdir -p /app/data && chown 1000:1000 /app/data

EXPOSE 7932

USER 1000:1000

CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "7932", "--timeout-graceful-shutdown", "5"]
