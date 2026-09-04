#!/usr/bin/env bash
# Create and manage per-person worktrees, each running its own full stack.
#
# Why this exists: three people running `npm run dev` in the same checkout fight
# over port 3000 and over app.db. A worktree gives each person a separate
# directory on a separate branch; this script additionally gives each one a
# distinct port pair so all three stacks can run at once and you can flip
# between them in the browser to compare behaviour.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKTREE_DIR="$REPO_ROOT/.worktrees"
PYTHON_VERSION=3.12

port_free() { ! lsof -i ":$1" -sTCP:LISTEN -t >/dev/null 2>&1; }

# Deterministic starting slot from the name, so the same worktree keeps the same
# ports across restarts, then linear probe until both ports are actually free.
allocate_ports() {
  local name="$1" slot base_web base_api
  slot=$(( $(printf '%s' "$name" | cksum | cut -d' ' -f1) % 20 ))
  for _ in $(seq 0 19); do
    base_web=$(( 3000 + slot ))
    base_api=$(( 8000 + slot ))
    if port_free "$base_web" && port_free "$base_api"; then
      printf '%s %s\n' "$base_web" "$base_api"
      return 0
    fi
    slot=$(( (slot + 1) % 20 ))
  done
  echo "no free port pair in 3000-3019 / 8000-8019" >&2
  return 1
}

# Dependency setup, worth explaining because the obvious approach fails:
#
#   Python — `uv sync` hardlinks from uv's global cache, so a real install in a
#   fresh worktree is ~0.1s warm. Genuine isolation, no reason to share.
#
#   Node — a symlinked node_modules is REJECTED by Turbopack ("Symlink
#   [project]/node_modules is invalid, it points out of the filesystem root"),
#   and `npm install` costs about a minute. On APFS, `cp -c` clones
#   copy-on-write: ~3s for 475MB and no real disk used until a file changes.
prepare_deps() {
  local target="$1" fresh="$2"
  ( cd "$target/api" && uv sync --python "$PYTHON_VERSION" --quiet )

  if [ "$fresh" = "fresh" ]; then
    ( cd "$target/web" && npm install )
  elif ! cp -Rc "$REPO_ROOT/web/node_modules" "$target/web/node_modules" 2>/dev/null; then
    # Not APFS, or no node_modules to clone from.
    echo "clone unavailable, falling back to npm install…"
    ( cd "$target/web" && npm install )
  fi
}

cmd_new() {
  local name="${1:-}" fresh="${2:-linked}"
  if [ -z "$name" ]; then
    echo "usage: worktree.sh new <name> [--fresh]" >&2
    exit 1
  fi
  local target="$WORKTREE_DIR/$name"
  if [ -e "$target" ]; then
    echo "worktree '$name' already exists at $target" >&2
    exit 1
  fi

  local web_port api_port
  read -r web_port api_port <<<"$(allocate_ports "$name")"

  git -C "$REPO_ROOT" worktree add -b "work/$name" "$target" >/dev/null
  cat > "$target/.worktree-env" <<ENVEOF
WORKTREE_NAME=$name
WEB_PORT=$web_port
API_PORT=$api_port
ENVEOF
  # Next.js inlines NEXT_PUBLIC_* into the browser bundle; this is what points
  # the UI at *this* worktree's API rather than whatever owns port 8000.
  cat > "$target/web/.env.local" <<ENVEOF
NEXT_PUBLIC_API_BASE=http://localhost:$api_port
NEXT_PUBLIC_WORKTREE=$name
ENVEOF
  prepare_deps "$target" "$fresh"

  cat <<INFO

worktree '$name' ready
  path   $target
  branch work/$name
  web    http://localhost:$web_port
  api    http://localhost:$api_port

  cd $target && ./scripts/dev.sh
INFO
}

cmd_list() {
  printf "%-14s %-22s %-7s %-7s %s\n" NAME BRANCH WEB API STATUS
  git -C "$REPO_ROOT" worktree list --porcelain \
    | awk '/^worktree /{ $1=""; sub(/^ /,""); print }' \
    | while read -r dir; do
        local_name=main; local_web=3000; local_api=8000
        if [ -f "$dir/.worktree-env" ]; then
          # shellcheck disable=SC1090
          . "$dir/.worktree-env"
          local_name="$WORKTREE_NAME"; local_web="$WEB_PORT"; local_api="$API_PORT"
        fi
        branch="$(git -C "$dir" branch --show-current 2>/dev/null || true)"
        if port_free "$local_web"; then status=stopped; else status=RUNNING; fi
        printf "%-14s %-22s %-7s %-7s %s\n" \
          "$local_name" "${branch:--}" "$local_web" "$local_api" "$status"
      done
}

cmd_rm() {
  local name="${1:-}"
  if [ -z "$name" ]; then
    echo "usage: worktree.sh rm <name> [--force]" >&2
    exit 1
  fi
  shift
  # "$@" rather than "${2:-}": an empty extra argument makes git exit 129.
  git -C "$REPO_ROOT" worktree remove "$WORKTREE_DIR/$name" "$@"
  echo "removed worktree '$name' (branch work/$name kept)"
}

case "${1:-}" in
  new)
    shift
    if [ "${2:-}" = "--fresh" ]; then cmd_new "$1" fresh; else cmd_new "${1:-}"; fi
    ;;
  list) cmd_list ;;
  rm)   shift; cmd_rm "$@" ;;
  *)
    echo "usage: worktree.sh {new <name> [--fresh] | list | rm <name>}" >&2
    exit 1
    ;;
esac
