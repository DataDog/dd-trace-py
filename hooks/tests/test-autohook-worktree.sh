#!/usr/bin/env sh
# Tests for hooks/autohook.sh install in a git worktree.
#
# Builds a throwaway repo + worktree so this never touches the caller's git config.
# Run with:  sh hooks/tests/test-autohook-worktree.sh

set -eu

AUTOHOOK="$(cd "$(dirname "$0")/.." && pwd)/autohook.sh"
PASS=0
FAIL=0

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

check() {
    name="$1"; condition="$2"
    if eval "$condition"; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name"
        FAIL=$((FAIL + 1))
    fi
}

REPO="$TMPDIR_TEST/repo"
WT="$TMPDIR_TEST/wt"
git init -q "$REPO"
git -C "$REPO" config user.email "hook-test@example.com"
git -C "$REPO" config user.name "hook-test"
mkdir -p "$REPO/hooks/pre-commit"
cp "$AUTOHOOK" "$REPO/hooks/autohook.sh"
chmod +x "$REPO/hooks/autohook.sh"
printf '%s\n' '#!/bin/sh' 'exit 0' > "$REPO/hooks/pre-commit/00-noop"
chmod +x "$REPO/hooks/pre-commit/00-noop"
git -C "$REPO" add hooks
git -C "$REPO" commit -q -m init

# The skip this install exists to undo: relative hooksPath in the *common* config.
git -C "$REPO" config --local core.hooksPath .git/hooks
git -C "$REPO" worktree add -q "$WT"

# install uses git rev-parse against cwd, so it must run from the worktree.
# Hide the caller's global hooksPath so --get-all cannot shadow the repo value.
( cd "$WT" && GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
    ./hooks/autohook.sh install >/dev/null )

COMMON_HOOKS="$(cd "$(git -C "$WT" rev-parse --git-common-dir)" && pwd)/hooks"
COMMON_CONFIG="$(cd "$(git -C "$WT" rev-parse --git-common-dir)" && pwd)/config"

check "install writes pre-commit into the common hooks dir" \
    "test -L '$COMMON_HOOKS/pre-commit'"
check "install writes post-merge into the common hooks dir" \
    "test -L '$COMMON_HOOKS/post-merge'"
check "install writes post-checkout into the common hooks dir" \
    "test -L '$COMMON_HOOKS/post-checkout'"
check "install unsets relative core.hooksPath from the common config" \
    "! git config --file '$COMMON_CONFIG' --get core.hooksPath"
check "worktree repo config has no relative core.hooksPath" \
    "test -z \"\$(GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null git -C '$WT' config --get core.hooksPath || true)\""

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
