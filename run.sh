#!/usr/bin/env bash
# Build (install dependencies) and run the recipe catalog web app.
#
# Usage:
#   ./run.sh                 # sync deps, start the dev server with --reload
#   ./run.sh --port 9000     # extra args are passed through to uvicorn
#   LLM_PROVIDER=fake ./run.sh   # run offline without an API key
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is not installed (see https://docs.astral.sh/uv/)" >&2
    exit 1
fi

echo "==> Syncing dependencies"
uv sync

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "==> Created .env from .env.example — set ANTHROPIC_API_KEY in it (or use LLM_PROVIDER=fake)"
fi

echo "==> Starting server"
exec uv run uvicorn app.main:app --reload "$@"
