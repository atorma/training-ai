FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd -g 1000 appgroup \
    && useradd -u 1000 -g 1000 -m -s /sbin/nologin appuser

WORKDIR /app

COPY --from=builder /wheels /wheels

RUN pip install --no-cache-dir --no-index --find-links /wheels training-ai

EXPOSE 7932

USER 1000:1000

CMD ["uvicorn", "web:app", "--host", "0.0.0.0", "--port", "7932"]
