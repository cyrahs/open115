# Builder
FROM python:3.13-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

# Runner
FROM python:3.13-slim-bookworm AS runner

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Copy venv and application code
COPY --from=builder /app/.venv .venv
COPY app/ app/
COPY entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENV PYTHONPATH=/app
ENV LOG_LEVEL=INFO
ENV APP_MODULE=app.main:app
ENV UVICORN_WORKERS=auto

ENTRYPOINT ["/entrypoint.sh"]
