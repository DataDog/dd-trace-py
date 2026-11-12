#!/usr/bin/env sh
# Tests for hooks/pre-commit/13-check-bridged-modules
#
# Stubs git and the bridge checker (via CHECK_CMD) so no real bridge surface
# is needed. Run with:  sh hooks/tests/test-check-bridged-modules.sh

set -eu

SCRIPT="$(cd "$(dirname "$0")/../pre-commit" && pwd)/13-check-bridged-modules"
PASS=0
FAIL=0

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

CHECK_CALLS="$TMPDIR_TEST/check-calls.txt"

mock_staged() {
    files="$1"
    cat > "$TMPDIR_TEST/git" << EOF
#!/usr/bin/env sh
case "\$*" in
    "diff --staged --name-only HEAD --diff-filter=ACMR")
        printf '%s\n' $files ;;
    *)
        exec "$(command -v git)" "\$@" ;;
esac
EOF
    chmod +x "$TMPDIR_TEST/git"
}

setup_check_mock() {
    status="${1:-0}"
    cat > "$TMPDIR_TEST/check-mock" << EOF
#!/usr/bin/env sh
echo "\$*" >> "$CHECK_CALLS"
exit $status
EOF
    chmod +x "$TMPDIR_TEST/check-mock"
    : > "$CHECK_CALLS"
}

run_hook() {
    PATH="$TMPDIR_TEST:$PATH" CHECK_CMD="$TMPDIR_TEST/check-mock" sh "$SCRIPT" 2>&1
}

check() {
    name="$1"; condition="$2"
    if eval "$condition"; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name  (check calls: $(cat "$CHECK_CALLS" 2>/dev/null || echo '<none>'))"
        FAIL=$((FAIL + 1))
    fi
}

setup_check_mock 0

mock_staged "docs/foo.md"
: > "$CHECK_CALLS"
output=$(run_hook)
check "unrelated file: check not called" "! [ -s '$CHECK_CALLS' ]"
check "unrelated file: skip message printed" "printf '%s' '$output' | grep -q 'skipped'"

mock_staged "ddtrace/internal/codeowners.py"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "bridged module: check runs" "grep -qF 'bridged-modules' '$CHECK_CALLS'"

mock_staged "pyproject.toml"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "pyproject.toml: check runs" "grep -qF 'bridged-modules' '$CHECK_CALLS'"

mock_staged "requirements.csv"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "requirements.csv: check runs" "grep -qF 'bridged-modules' '$CHECK_CALLS'"

mock_staged "scripts/package_split/bridge.py"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "bridge tooling: check runs" "grep -qF 'bridged-modules' '$CHECK_CALLS'"

mock_staged "tests/internal/test_codeowners.py"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "test-only change: check not called" "! [ -s '$CHECK_CALLS' ]"

setup_check_mock 2
mock_staged "ddtrace/internal/codeowners.py"
: > "$CHECK_CALLS"
set +e
run_hook > /dev/null
hook_rc=$?
set -e
check "failed check: hook exits non-zero" "[ '$hook_rc' -ne 0 ]"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
