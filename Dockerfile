# syntax=docker/dockerfile:1
# uv's standalone CPython bundles a recent SQLite; Debian's system SQLite lags behind. Using it
# keeps production on the same Python and SQLite as local development and tests.
ARG PYTHON_VERSION=3.12.15
ARG DEBIAN_IMAGE=debian:trixie-slim

FROM ${DEBIAN_IMAGE} AS dependencies
ARG PYTHON_VERSION

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PYTHON=${PYTHON_VERSION} \
    UV_LINK_MODE=copy

ARG TARGETARCH
RUN --mount=type=cache,id=training-ai-uv-${TARGETARCH},target=/root/.cache/uv \
    uv python install "$PYTHON_VERSION"
# Every later step must use the interpreter installed above.
ENV UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
# This layer changes only when dependency metadata or the lockfile changes.
RUN --mount=type=cache,id=training-ai-uv-${TARGETARCH},target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM dependencies AS builder

COPY README.md ./
COPY src ./src
# Built locally by make build-web before invoking Docker.
COPY src/training_ai/web_assets/static ./src/training_ai/web_assets/static

RUN --mount=type=cache,id=training-ai-uv-${TARGETARCH},target=/root/.cache/uv \
    test -s src/training_ai/web_assets/static/index.html \
    && uv sync --locked --no-dev --no-editable \
    && uv pip check --python /opt/venv/bin/python

FROM ${DEBIAN_IMAGE}

# The python:*-slim images provided these: TLS roots, time zone data and a UTF-8 locale.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    PATH="/opt/venv/bin:$PATH"

RUN groupadd -g 1000 appgroup \
    && useradd -u 1000 -g 1000 -m -s /sbin/nologin appuser

WORKDIR /app

# The virtual environment links to this interpreter, so it keeps the same path.
COPY --from=builder /opt/python /opt/python
COPY --from=builder /opt/venv /opt/venv

RUN mkdir -p /app/data && chown 1000:1000 /app/data

EXPOSE 7932

USER 1000:1000

CMD ["uvicorn", "training_ai.web:app", "--host", "0.0.0.0", "--port", "7932", "--timeout-graceful-shutdown", "5"]
