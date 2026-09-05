#!/usr/bin/env bash
# Install the pre-commit check so it covers EVERY worktree, on every branch.
#
# The trap this exists to avoid: `core.hooksPath = .githooks` is written to the
# *shared* config, so all worktrees of this repo inherit the setting the moment
# one of them sets it -- but the path is relative, and git resolves it against
# the invoking worktree's root. `.githooks/` only exists on branches that carry
# it, so a worktree on an older branch gets the setting, finds nothing, runs no
# hook, and prints nothing. Measured: work/mcp and work/agent-forensics both had
# the setting and no protection.
#
# So the hook goes in the shared hooks directory instead, which every worktree
# consults by construction and which no branch can take away. Verified in a
# throwaway repo: a hook there fires from a linked worktree, and
# `git rev-parse --show-toplevel` inside it reports the *invoking* worktree,
# which is what the check keys off.
#
# What gets installed is a shim, not a copy, so the version-controlled hook
# stays the source of truth: the shim prefers `<worktree>/.githooks/pre-commit`
# and only falls back to the snapshot beside it on a branch that has no such
# file. Edit `.githooks/pre-commit`; re-run this to refresh the snapshot.
set -euo pipefail

TOP="$(git rev-parse --show-toplevel)"
HOOKS="$(git rev-parse --git-common-dir)/hooks"
SRC="$TOP/.githooks/pre-commit"

[ -x "$SRC" ] || { echo "no $SRC on this branch -- nothing to install." >&2; exit 1; }

mkdir -p "$HOOKS"
cp "$SRC" "$HOOKS/pre-commit.aivar"
chmod +x "$HOOKS/pre-commit.aivar"

cat > "$HOOKS/pre-commit" <<'SHIM'
#!/usr/bin/env bash
# Installed by .githooks/install.sh. Do not edit here -- edit .githooks/pre-commit
# in the working tree; this prefers that file whenever the branch has it.
set -uo pipefail
TOP="$(git rev-parse --show-toplevel 2>/dev/null || true)"
HOOK="$TOP/.githooks/pre-commit"
if [ ! -x "$HOOK" ]; then
  HOOK="$(cd "$(dirname "$0")" && pwd)/pre-commit.aivar"
fi
if [ ! -x "$HOOK" ]; then
  # Loud, not silent, and not fatal. Silence is the failure this whole hook
  # exists to prevent; blocking every commit in every worktree because an
  # install went wrong is a worse cure than the disease.
  echo "pre-commit: WARNING -- no hook found, this commit is UNCHECKED." >&2
  echo "            re-run: cd app && make hooks" >&2
  exit 0
fi
exec "$HOOK" "$@"
SHIM
chmod +x "$HOOKS/pre-commit"

# Remove the relative setting: with it unset, git falls back to the shared hooks
# directory, which is the whole point. Left in place it would keep pointing
# older branches at a directory they do not have.
git config --unset core.hooksPath 2>/dev/null || true

echo "installed -> $HOOKS/pre-commit (shim) + pre-commit.aivar (snapshot)"
echo "core.hooksPath unset; the shared hooks dir now covers every worktree:"
git worktree list | sed 's/^/    /'
