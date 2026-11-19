#!/usr/bin/env sh
# Tests for hooks/autohook.sh install in a git worktree.
#
# Builds a throwaway repo + worktree so this never touches the caller's git config.
# Run with:  sh hooks/tests/test-autohook-worktree.sh

set -eu

AUTOHOOK="$(cd "$(dirname "$0")/.." && pwd)/autohook.sh"
PASS=0
FAIL=0

# Do not let user or system Git configuration affect setup or probes.
export GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_SYSTEM=/dev/null

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
git -C "$REPO" config extensions.worktreeConfig true
mkdir -p "$REPO/hooks/pre-commit"
cp "$AUTOHOOK" "$REPO/hooks/autohook.sh"
chmod +x "$REPO/hooks/autohook.sh"
printf '%s\n' '#!/bin/sh' 'exit 0' > "$REPO/hooks/pre-commit/00-noop"
chmod +x "$REPO/hooks/pre-commit/00-noop"
git -C "$REPO" add hooks
git -C "$REPO" commit -q -m init

# The install below must remove the old value from both common and worktree config.
git -C "$REPO" config --local core.hooksPath .git/hooks
git -C "$REPO" worktree add -q "$WT"
git -C "$WT" config --worktree core.hooksPath .git/hooks

# ---- without fix: old $repo_root/.git/hooks + relative hooksPath ----
# Old install wrote $repo_root/.git/hooks. In a worktree that path is not a
# directory (.git is a file), and relative core.hooksPath=.git/hooks resolves
# against the worktree root so a hook in the common dir never runs.
WITHOUT_MARK="$TMPDIR_TEST/without-hook-ran"
mkdir -p "$REPO/.git/hooks"
printf '%s\n' '#!/bin/sh' "echo HOOK_RAN > '$WITHOUT_MARK'" 'exit 1' \
    > "$REPO/.git/hooks/pre-commit"
chmod +x "$REPO/.git/hooks/pre-commit"
ln -s "$TMPDIR_TEST/missing-hook" "$REPO/.git/hooks/post-merge"

check "without fix: worktree .git is a file" "test -f '$WT/.git'"
check "without fix: old \$repo_root/.git/hooks is not a directory" \
    "test ! -d '$WT/.git/hooks'"

printf 'x\n' > "$WT/without-probe"
git -C "$WT" add without-probe
WITHOUT_RC=0
WITHOUT_OUT=$(
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
        git -C "$WT" commit -m without-probe 2>&1
) || WITHOUT_RC=$?

echo "without fix: commit rc=$WITHOUT_RC"
echo "$WITHOUT_OUT" | sed -n '1,3p'
check "without fix: commit succeeds because the hook is skipped" \
    "[ '$WITHOUT_RC' -eq 0 ]"
check "without fix: sentinel never written (silent skip)" \
    "test ! -f '$WITHOUT_MARK'"

# install uses git rev-parse against cwd, so it must run from the worktree.
# Hide the caller's global hooksPath so --get-all cannot shadow the repo value.
( cd "$WT" && GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
    ./hooks/autohook.sh install >/dev/null )

COMMON_HOOKS="$(cd "$(git -C "$WT" rev-parse --git-common-dir)" && pwd)/hooks"
COMMON_CONFIG="$(cd "$(git -C "$WT" rev-parse --git-common-dir)" && pwd)/config"
WORKTREE_CONFIG="$(git -C "$WT" rev-parse --path-format=absolute --git-path config.worktree)"

check "install writes pre-commit into the common hooks dir" \
    "test -L '$COMMON_HOOKS/pre-commit'"
check "install writes post-merge into the common hooks dir" \
    "test -L '$COMMON_HOOKS/post-merge'"
check "install writes post-checkout into the common hooks dir" \
    "test -L '$COMMON_HOOKS/post-checkout'"
check "install replaces a dangling hook symlink" \
    "test -L '$COMMON_HOOKS/post-merge' && test -e '$COMMON_HOOKS/post-merge'"
check "install unsets relative core.hooksPath from the common config" \
    "! git config --file '$COMMON_CONFIG' --get core.hooksPath"
check "install unsets old core.hooksPath from worktree config" \
    "! git config --file '$WORKTREE_CONFIG' --get core.hooksPath"
check "worktree repo config has no relative core.hooksPath" \
    "test -z \"\$(git -C '$WT' config --get core.hooksPath || true)\""

# A valid relative path is preserved and remains the effective hook directory.
git -C "$WT" config --local core.hooksPath .githooks
( cd "$WT" && ./hooks/autohook.sh install >/dev/null )
CUSTOM_HOOKS="$WT/.githooks"
check "install preserves a valid relative core.hooksPath" \
    "[ \"\$(git -C '$WT' config --get core.hooksPath)\" = .githooks ]"
check "install writes valid custom hook symlink" \
    "test -L '$CUSTOM_HOOKS/pre-commit' && test -e '$CUSTOM_HOOKS/pre-commit'"

# ---- with fix: resolved hooksPath + hook runs ----
# Autohook reads the worktree's configured hooks path.
WITH_MARK="$TMPDIR_TEST/with-hook-ran"
printf '%s\n' '#!/bin/sh' "echo HOOK_RAN > '$WITH_MARK'" 'exit 1' \
    > "$WT/hooks/pre-commit/01-sentinel"
chmod +x "$WT/hooks/pre-commit/01-sentinel"

printf 'y\n' > "$WT/with-probe"
git -C "$WT" add with-probe
WITH_RC=0
WITH_OUT=$(
    GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null \
        git -C "$WT" commit -m with-probe 2>&1
) || WITH_RC=$?

echo "with fix: commit rc=$WITH_RC"
echo "$WITH_OUT" | sed -n '1,8p'
check "with fix: commit is blocked because the hook ran" \
    "[ '$WITH_RC' -ne 0 ]"
check "with fix: sentinel written (HOOK_RAN)" \
    "test -f '$WITH_MARK'"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
