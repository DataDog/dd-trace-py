#!/usr/bin/env sh
# Tests for hooks/scripts/hook-telemetry.sh
#
# Run with: sh hooks/tests/test-hook-telemetry.sh

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")/../scripts" && pwd)"
TELEMETRY="$SCRIPT_DIR/hook-telemetry.sh"
PASS=0
FAIL=0

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

GLOBAL_CFG="$TMPDIR_TEST/global.gitconfig"
METRICS_FILE="$TMPDIR_TEST/metrics.log"
export GIT_CONFIG_GLOBAL="$GLOBAL_CFG"
export GIT_CONFIG_SYSTEM=/dev/null
export DD_HOOK_TELEMETRY=1
export DD_HOOK_TELEMETRY_FILE="$METRICS_FILE"

assert_contains() {
    needle="$1"
    label="$2"
    if grep -Fq "$needle" "$METRICS_FILE" 2>/dev/null; then
        PASS=$((PASS + 1))
    else
        echo "FAIL: $label (missing '$needle' in $METRICS_FILE)" >&2
        FAIL=$((FAIL + 1))
    fi
}

setup_repo() {
    rm -rf "$TMPDIR_TEST/repo"
    mkdir -p "$TMPDIR_TEST/repo"
    cat >"$GLOBAL_CFG" <<EOF
[core]
	hooksPath = /usr/local/dd/global_hooks
EOF
    (
        cd "$TMPDIR_TEST/repo"
        git init -q
        git remote add origin git@github.com:DataDog/dd-trace-py.git
    )
}

: >"$METRICS_FILE"
setup_repo
(
    cd "$TMPDIR_TEST/repo"
    # shellcheck source=/dev/null
    . "$TELEMETRY"
    dd_hook_telemetry_autohook_execution "pre-commit" "false" "3"
)
assert_contains "dd.trace.repo_hooks.autohook.executions" "autohook metric name"
assert_contains "hook:pre-commit" "autohook hook tag"
assert_contains "github_repo:dd-trace-py" "autohook repo tag"
assert_contains "hook_chain_fix:ensure-dd-hook-chain-v1" "attribution tag"

: >"$METRICS_FILE"
setup_repo
(
    cd "$TMPDIR_TEST/repo"
    # shellcheck source=/dev/null
    . "$TELEMETRY"
    dd_hook_telemetry_hook_chain_bypass "git_hooks_override"
    dd_hook_telemetry_hook_chain_repair
)
assert_contains "dd.trace.repo_hooks.hook_chain.bypass" "bypass metric name"
assert_contains "bypass_kind:git_hooks_override" "bypass kind tag"
assert_contains "dd.trace.repo_hooks.hook_chain.repair" "repair metric name"
assert_contains "outcome:repaired" "repair outcome tag"

echo "test-hook-telemetry: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
