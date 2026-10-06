#!/usr/bin/env bash
# One-command start: creates a venv, installs deps, runs tests (fast), starts the server.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
./.venv/bin/pip install -q --upgrade pip
./.venv/bin/pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo ">> created .env from .env.example - add your keys (keyless feeds work without any)"; }
./.venv/bin/python -m pytest -q backend/tests
HOST="$(grep -E '^HOST=' .env | cut -d= -f2 || true)"; PORT="$(grep -E '^PORT=' .env | cut -d= -f2 || true)"
echo ">> http://${HOST:-127.0.0.1}:${PORT:-8700}"
exec ./.venv/bin/uvicorn backend.app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8700}"
