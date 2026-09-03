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

port_free() { ! lsof -i ":$1" -sTCP:LISTEN -t >/dev/null 2>&1; }

# Deterministic starting slot from the name, so the same worktree keeps the same
# ports across restarts, then linear probe until both ports are actually free.
allocate_ports() {
  local name="$1" slot base_web base_api
  slot=$(( $(echo -n "$name" | cksum | cut -d' ' -f1) % 20 ))
  for _ in $(seq 0 19); do
    base_web=$(( 3000 + slot ))
    base_api=$(( 8000 + slot ))
    if port_free "$base_web" && port_free "$base_api"; then
      echo "$base_web $base_api"
      return 0
    fi
    slot=$(( (slot + 1) % 20 ))
  done
  echo "no free port pair in 3000-3019 / 8000-8019" >&2
  return 1
}

# Dependency install is the slow part of a new worktree, and every worktree has
# byte-identical dependencies. Symlinking makes a new worktree instant.
# Caveat: `npm install <pkg>` inside a worktree mutates the shared tree, so run
# `--fresh` if a worktree needs its own dependency set.
link_deps() {
  local target="$1" fresh="$2"
  if [ "$fresh" = "fresh" ]; then
    ( cd "$target/web" && npm install )
    ( cd "$target/api" && uv sync --python 3.12 )
  else
    ln -sfn "$REPO_ROOT/web/node_modules" "$target/web/node_modules"
    ln -sfn "$REPO_ROOT/api/.venv" "$target/api/.venv"
  fi
}

cmd_new() {
  local name="${1:-}" fresh="${2:-linked}"
  [ -n "$name" ] || { echo "usage: worktree.sh new <name> [--fresh]" >&2; exit 1; }
  local target="$WORKTREE_DIR/$name"
  [ -e "$target" ] && { echo "worktree '$name' already exists at $target" >&2; exit 1; }

  read -r web_port api_port <<<"$(allocate_ports "$name")"

  git -C "$REPO_ROOT" worktree add -b "work/$name" "$target" >/dev/null
  cat > "$target/.worktree-env" <<ENVEOF
WORKTREE_NAME=$name
WEB_PORT=$web_port
API_PORT=$api_port
ENVEOF
  # Next.js reads NEXT_PUBLIC_* at build time; this is what points the browser
  # bundle at *this* worktree's API rather than the one on 8000.
  cat > "$target/web/.env.local" <<ENVEOF
NEXT_PUBLIC_API_BASE=http://localhost:$api_port
NEXT_PUBLIC_WORKTREE=$name
ENVEOF
  link_deps "$target" "$fresh"

  echo "worktree '$name' ready"
  echo "  path   $target"
  echo "  branch work/$name"
  echo "  web    http://localhost:$web_port"
  echo "  api    http://localhost:$api_port"
  echo
  echo "  cd $target && ./scripts/dev.sh"
}

cmd_list() {
  printf "%-14s %-22s %-8s %-8s %s\n" NAME BRANCH WEB API STATUS
  for env_file in "$REPO_ROOT/.worktree-env" "$WORKTREE_DIR"/*/.worktree-env; do
    [ -f "$env_file" ] || continue
    ( # shellcheck disable=SC1090
      set -a; . "$env_file"; set +a
      local_dir="$(dirname "$env_file")"
      branch="$(git -C "$local_dir" branch --show-current 2>/dev/null || echo '-')"
      if port_free "$WEB_PORT"; then status="stopped"; else status="RUNNING"; fi
      printf "%-14s %-22s %-8s %-8s %s\n" \
        "$WORKTREE_NAME" "$branch" "$WEB_PORT" "$API_PORT" "$status"
    )
  done
}

cmd_rm() {
  local name="${1:-}"
  [ -n "$name" ] || { echo "usage: worktree.sh rm <name>" >&2; exit 1; }
  git -C "$REPO_ROOT" worktree remove "$WORKTREE_DIR/$name" "${2:-}"
  echo "removed worktree '$name' (branch work/$name kept)"
}

case "${1:-}" in
  new)  shift; [ "${2:-}" = "--fresh" ] && set -- "$1" fresh; cmd_new "$@" ;;
  list) cmd_list ;;
  rm)   shift; cmd_rm "$@" ;;
  *)    echo "usage: worktree.sh {new <name> [--fresh] | list | rm <name>}" >&2; exit 1 ;;
esac
