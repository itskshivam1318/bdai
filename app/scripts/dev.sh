#!/usr/bin/env bash
# Run the full stack for THIS worktree. Reads .worktree-env if present, falls
# back to 3000/8000 so the main checkout works with no setup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORKTREE_NAME=main; WEB_PORT=3000; API_PORT=8000
if [ -f .worktree-env ]; then set -a; . ./.worktree-env; set +a; fi

echo "── $WORKTREE_NAME ──  web :$WEB_PORT   api :$API_PORT"

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

( cd api && uv run uvicorn app.main:app --reload --port "$API_PORT" ) &
pids+=($!)

# Passed explicitly as well as via .env.local so the main worktree (which has no
# .env.local) still points at the right API.
( cd web && NEXT_PUBLIC_API_BASE="http://localhost:$API_PORT" \
    NEXT_PUBLIC_WORKTREE="$WORKTREE_NAME" \
    npm run dev -- --port "$WEB_PORT" ) &
pids+=($!)

wait
