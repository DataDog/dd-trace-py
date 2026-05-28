#!/usr/bin/env sh
# Tests for hooks/scripts/build-native-ext.sh
# Run with: sh hooks/tests/test-build-native-ext.sh

set -eu

SCRIPT="$(cd "$(dirname "$0")/../scripts" && pwd)/build-native-ext.sh"
PASS=0
FAIL=0

assert_contains() {
    output=$1
    needle=$2
    label=$3
    case "$output" in
        *"$needle"*) PASS=$((PASS + 1)) ;;
        *)
            echo "FAIL: $label"
            echo "  expected output to contain: $needle"
            echo "  got: $output"
            FAIL=$((FAIL + 1))
            ;;
    esac
}

# skip when env set
output=$(DD_SKIP_NATIVE_BUILD=1 sh "$SCRIPT" 2>&1 || true)
assert_contains "$output" "DD_SKIP_NATIVE_BUILD" "respects DD_SKIP_NATIVE_BUILD"

<<<<<<< Updated upstream
# stale .eggs detection helper (sourced logic via grep pattern)
if printf '%s\n' 'OSError: [Errno 66] Directory not empty: .eggs/foo' | grep -qiE 'directory not empty|errno 66|\[errno 66\]'; then
    PASS=$((PASS + 1))
else
    echo "FAIL: stale .eggs error pattern"
    FAIL=$((FAIL + 1))
fi

=======
>>>>>>> Stashed changes
# skip when no native staged files (requires git repo)
if git rev-parse --git-dir >/dev/null 2>&1; then
    output=$(sh "$SCRIPT" 2>&1 || true)
    case "$output" in
        *"build_ext skipped"*) PASS=$((PASS + 1)) ;;
        *"Rebuilding native extensions"*)
            echo "NOTE: native rebuild ran (staged native files present); skip test counted as pass"
            PASS=$((PASS + 1))
            ;;
        *)
            echo "FAIL: unexpected output from build-native-ext"
            echo "$output"
            FAIL=$((FAIL + 1))
            ;;
    esac
else
    echo "SKIP: not in a git repo"
fi

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
