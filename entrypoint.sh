#!/bin/sh
set -eu
APP_MODULE="${APP_MODULE:-app.main:app}"
HOST="${UVICORN_HOST:-0.0.0.0}"
PORT="${UVICORN_PORT:-8000}"
LOOP_IMPL="${UVICORN_LOOP:-uvloop}"
HTTP_IMPL="${UVICORN_HTTP:-httptools}"

VENV_BIN="${VENV_BIN:-/app/.venv/bin}"
PYTHON_BIN="${PYTHON_BIN:-$VENV_BIN/python}"
UVICORN_BIN="${UVICORN_BIN:-$VENV_BIN/uvicorn}"
TOKEN_MANAGER_CMD="${TOKEN_MANAGER_CMD:-$PYTHON_BIN -m app.service.token_manager}"

$TOKEN_MANAGER_CMD &
MANAGER_PID=$!

abort() {
  kill "$MANAGER_PID" 2>/dev/null || true
  wait "$MANAGER_PID" 2>/dev/null || true
  exit 1
}

if ! "$PYTHON_BIN" - <<'PY'
import asyncio
from app.service import open115

async def main():
    await open115.ensure_tokens_ready(timeout=60)

asyncio.run(main())
PY
then
  echo "Token store was not initialised by the token manager." >&2
  abort
fi

WORKERS_INPUT="${UVICORN_WORKERS:-auto}"
if [ "$WORKERS_INPUT" = "auto" ] || [ -z "$WORKERS_INPUT" ]; then
  WORKERS=$("$PYTHON_BIN" - <<'PY'
import multiprocessing

print(max(1, multiprocessing.cpu_count()))
PY
)
else
  WORKERS="$WORKERS_INPUT"
fi

exec "$UVICORN_BIN" "$APP_MODULE" \
  --host "$HOST" \
  --port "$PORT" \
  --loop "$LOOP_IMPL" \
  --http "$HTTP_IMPL" \
  --workers "$WORKERS" \
  "$@"
