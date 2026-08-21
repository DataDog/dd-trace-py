#!/usr/bin/env bash
#
# Keep Datadog global git hooks (dd-git-hooks secrets scan) in front of repo
# hooks. A local core.hooksPath override — often added as a pre-commit install
# workaround — makes git skip /usr/local/dd/global_hooks entirely.
#
# Safe layout on DD laptops:
#   global pre-commit -> dd-git-hooks -> run-local-hooks -> .git/hooks (autohook)
#
# Usage: hooks/scripts/ensure-dd-hook-chain.sh [--quiet]

set -euo pipefail

quiet=0
if [ "${1:-}" = "--quiet" ]; then
  quiet=1
fi

log() {
  if [ "$quiet" -eq 0 ]; then
    echo "[Autohook] $*" >&2
  fi
}

repo_root="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$repo_root"

global_hooks="$(git config --global --get core.hooksPath 2>/dev/null || true)"
# Only repair when the DD-managed global hook chain is configured. An unrelated
# global hooksPath plus a deliberate local .git/hooks override may be intentional.
if [ "$global_hooks" != "/usr/local/dd/global_hooks" ]; then
  exit 0
fi

local_hooks="$(git config --local --get core.hooksPath 2>/dev/null || true)"
if [ -z "$local_hooks" ]; then
  exit 0
fi

resolve_hooks_path() {
  local path="$1"
  if [ "${path#/}" = "$path" ]; then
    path="$repo_root/$path"
  fi
  local dir base
  dir="$(dirname "$path")"
  base="$(basename "$path")"
  if [ ! -d "$dir" ]; then
    return 1
  fi
  echo "$(cd "$dir" && pwd -P)/$base"
}

if ! local_abs="$(resolve_hooks_path "$local_hooks")"; then
  log "Local core.hooksPath=$local_hooks is not a resolvable path; leaving it unchanged."
  exit 0
fi

if ! repo_hooks_dir="$(resolve_hooks_path "$(git rev-parse --git-dir)/hooks")"; then
  exit 0
fi

# Only remove overrides that point at this repo's .git/hooks. Other local values
# may be intentional (worktree setups, etc.).
if [ "$local_abs" != "$repo_hooks_dir" ]; then
  log "Local core.hooksPath=$local_hooks is set; leaving it unchanged."
  log "If commits skip DD secrets scanning, ask #security-help or see hooks/README.md."
  exit 0
fi

git config --local --unset-all core.hooksPath
log "Removed local core.hooksPath=$local_hooks (it bypassed DD secrets scanning)."
log "Repo hooks still run via global run-local-hooks after dd-git-hooks."
if [ ! -e "$repo_hooks_dir/pre-commit" ]; then
  log "Run: hooks/autohook.sh install"
fi
