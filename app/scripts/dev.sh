#!/usr/bin/env bash
# Run the full stack for THIS worktree. Reads .worktree-env if present, falls
# back to 3000/8000 so the main checkout works with no setup.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

WORKTREE_NAME=main; WEB_PORT=3000; API_PORT=8000
if [ -f .worktree-env ]; then set -a; . ./.worktree-env; set +a; fi

echo "── $WORKTREE_NAME ──  web :$WEB_PORT   api :$API_PORT"

# Reclaim our own ports before binding. A previous `make dev` that was
# backgrounded, or killed without its trap running, still holds them and the new
# run dies on EADDRINUSE.
#
# Only processes whose cwd is inside THIS checkout are killed. Every worktree
# without a .worktree-env falls back to 3000/8000, so several checkouts claim
# the same pair -- a blind kill-by-port would take down a peer session's stack,
# which is exactly the working behaviour we are not allowed to break. A foreign
# holder is reported and the run stops instead.
reclaim() {
  local port="$1" pid cwd foreign=() mine=()

  for pid in $(lsof -nP -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | sort -u); do
    # An unreadable cwd reads as foreign: we only kill what we can prove is ours.
    cwd="$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
    case "$cwd" in
      "$ROOT" | "$ROOT"/*) mine+=("$pid") ;;
      *) foreign+=("$pid ${cwd:-<unreadable cwd>}") ;;
    esac
  done

  if [ ${#foreign[@]} -gt 0 ]; then
    echo "   :$port is held by another checkout, not this one:" >&2
    printf '     pid %s\n' "${foreign[@]}" >&2
    echo "   give this worktree its own ports (app/.worktree-env) or stop that stack." >&2
    exit 1
  fi

  if [ ${#mine[@]} -eq 0 ]; then return 0; fi

  echo "   reclaiming :$port from pid ${mine[*]}"
  kill "${mine[@]}" 2>/dev/null || true

  # Wait for the port to actually be free rather than sleeping a guessed amount:
  # uvicorn --reload has to reap its child before the listener goes away.
  for _ in $(seq 1 50); do
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1 || return 0
    sleep 0.1
  done

  echo "   :$port did not free after SIGTERM, escalating" >&2
  kill -9 "${mine[@]}" 2>/dev/null || true
  for _ in $(seq 1 20); do
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1 || return 0
    sleep 0.1
  done

  echo "   :$port is still in use. Not starting." >&2
  exit 1
}

reclaim "$API_PORT"
reclaim "$WEB_PORT"

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
