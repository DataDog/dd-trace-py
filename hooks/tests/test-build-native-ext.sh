#!/usr/bin/env sh
# Tests for hooks/scripts/build-native-ext.sh
# Run with: sh hooks/tests/test-build-native-ext.sh

set -eu

export DDTRACE_NATIVE_BUILD_NONINTERACTIVE=1

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
assert_contains "$output" "DD_SKIP_NATIVE_BUILD" "respects DD_SKIP_NATIVE_BUILD via off mode"

# stale .eggs detection helper (same pattern as build-native-ext.sh)
if printf '%s\n' 'OSError: [Errno 66] Directory not empty: .eggs/foo' | grep -qiE 'directory not empty|errno 66|\[errno 66\]'; then
    PASS=$((PASS + 1))
else
    echo "FAIL: stale .eggs error pattern"
    FAIL=$((FAIL + 1))
fi

# test-only paths excluded from product native filter
if printf '%s\n' 'ddtrace/internal/foo_test.cpp' | grep -vE '(^|/)(test|tests|fuzz)(/|$)|/dd_wrapper/test/|_test\.cpp$' | grep -q .; then
    echo "FAIL: _test.cpp should be excluded"
    FAIL=$((FAIL + 1))
else
    PASS=$((PASS + 1))
fi

if printf '%s\n' 'dd_wrapper/test/test_foo.cpp' | grep -vE '(^|/)(test|tests|fuzz)(/|$)|/dd_wrapper/test/|_test\.cpp$' | grep -q .; then
    echo "FAIL: dd_wrapper/test paths should be excluded"
    FAIL=$((FAIL + 1))
else
    PASS=$((PASS + 1))
fi

if printf '%s\n' 'ddtrace/profiling/collector/stack.cpp' | grep -vE '(^|/)(test|tests|fuzz)(/|$)|/dd_wrapper/test/|_test\.cpp$' | grep -q .; then
    PASS=$((PASS + 1))
else
    echo "FAIL: product .cpp should not be excluded"
    FAIL=$((FAIL + 1))
fi

# git config off mode (requires git repo)
if git rev-parse --git-dir >/dev/null 2>&1; then
    saved_mode=$(git config --local --get ddtrace.nativeBuildMode 2>/dev/null || true)
    git config --local ddtrace.nativeBuildMode off
    output=$(sh "$SCRIPT" 2>&1 || true)
    assert_contains "$output" "ddtrace.nativeBuildMode=off" "respects git config off"
    git config --local ddtrace.nativeBuildMode warn
    output=$(sh "$SCRIPT" 2>&1 || true)
    case "$output" in
        *"native build hook skipped"*) PASS=$((PASS + 1)) ;;
        *"STAGED NATIVE/BUILD FILES"*)
            assert_contains "$output" "git config --local ddtrace.nativeBuildMode" "warn mode mentions git config"
            ;;
        *"Rebuilding native extensions"*)
            if [ "${DD_BUILD_NATIVE_ON_COMMIT:-}" = "1" ]; then
                PASS=$((PASS + 1))
            else
                saved=$(git config --local --get ddtrace.nativeBuildMode 2>/dev/null || true)
                if [ "$saved" = "block" ]; then
                    PASS=$((PASS + 1))
                else
                    echo "FAIL: rebuild ran without block mode"
                    echo "$output"
                    FAIL=$((FAIL + 1))
                fi
            fi
            ;;
        *)
            echo "FAIL: unexpected output from build-native-ext"
            echo "$output"
            FAIL=$((FAIL + 1))
            ;;
    esac

    if [ -n "$saved_mode" ]; then
        git config --local ddtrace.nativeBuildMode "$saved_mode"
    else
        git config --local --unset ddtrace.nativeBuildMode 2>/dev/null || true
    fi
else
    echo "SKIP: not in a git repo"
fi

echo ""
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
