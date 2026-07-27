#!/usr/bin/env sh
# Tests for hooks/scripts/ensure-dd-hook-chain.sh
#
# Uses GIT_CONFIG_GLOBAL so no real laptop git config is touched.
# Run with: sh hooks/tests/test-ensure-dd-hook-chain.sh

set -eu

SCRIPT="$(cd "$(dirname "$0")/../scripts" && pwd)/ensure-dd-hook-chain.sh"
PASS=0
FAIL=0

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

GLOBAL_CFG="$TMPDIR_TEST/global.gitconfig"
export GIT_CONFIG_GLOBAL="$GLOBAL_CFG"
export GIT_CONFIG_SYSTEM=/dev/null

assert_equals() {
    expected="$1"
    actual="$2"
    label="$3"
    if [ "$expected" = "$actual" ]; then
        PASS=$((PASS + 1))
    else
        echo "FAIL: $label (expected '$expected', got '$actual')" >&2
        FAIL=$((FAIL + 1))
    fi
}

setup_repo() {
    global_hooks_path="${1:-}"
    local_hooks_path="${2:-}"

    rm -rf "$TMPDIR_TEST/repo"
    mkdir -p "$TMPDIR_TEST/repo"

    if [ -n "$global_hooks_path" ]; then
        cat >"$GLOBAL_CFG" <<EOF
[core]
	hooksPath = $global_hooks_path
EOF
    else
        printf '' >"$GLOBAL_CFG"
    fi

    (
        cd "$TMPDIR_TEST/repo"
        git init -q
        if [ -n "$local_hooks_path" ]; then
            git config --local core.hooksPath "$local_hooks_path"
        fi
    )
}

run_ensure() {
    (
        cd "$TMPDIR_TEST/repo"
        "$SCRIPT" --quiet
    )
}

local_hooks_path() {
    cd "$TMPDIR_TEST/repo" && git config --local --get core.hooksPath 2>/dev/null || true
}

# --- tests ---

setup_repo "" ""
run_ensure
assert_equals "" "$(local_hooks_path)" "no global hooksPath: local unchanged"

setup_repo "/usr/local/dd/global_hooks" ""
run_ensure
assert_equals "" "$(local_hooks_path)" "global only: local still unset"

setup_repo "/usr/local/dd/global_hooks" ".git/hooks"
run_ensure
assert_equals "" "$(local_hooks_path)" "local .git/hooks override removed"

setup_repo "/usr/local/dd/global_hooks" "/tmp/custom-hooks"
run_ensure
assert_equals "/tmp/custom-hooks" "$(local_hooks_path)" "unrelated local hooksPath left alone"

setup_repo "/tmp/custom-global-hooks" ".git/hooks"
run_ensure
assert_equals ".git/hooks" "$(local_hooks_path)" "non-DD global hooksPath: local .git/hooks left alone"

setup_repo "/usr/local/dd/global_hooks" "/tmp/does-not-exist/hooks"
run_ensure
assert_equals "/tmp/does-not-exist/hooks" "$(local_hooks_path)" "non-existent local hooksPath left alone"

echo "test-ensure-dd-hook-chain: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
