#!/usr/bin/env sh
# Tests for hooks/pre-commit/04-run-clang-format
#
# Stubs git and clang-format so no real git repo or formatter is needed.
# Run with:  sh hooks/tests/test-run-clang-format.sh

set -eu

HOOK="$(cd "$(dirname "$0")/../pre-commit" && pwd)/04-run-clang-format"
PASS=0
FAIL=0

# ── helpers ──────────────────────────────────────────────────────────────────

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

CALLS_FILE="$TMPDIR_TEST/clang-format-calls.txt"

# Writes a mock git that emits $1 (newline-separated filenames) for the
# staged-files query, answers `rev-parse --show-toplevel` with $2 (default
# $TMPDIR_TEST, which has no .venv-lint), and delegates everything else to the
# real git.
mock_staged() {
    files="$1"
    root="${2:-$TMPDIR_TEST}"
    cat > "$TMPDIR_TEST/git" << EOF
#!/usr/bin/env sh
case "\$*" in
    "diff --staged --name-only HEAD --diff-filter=ACMR")
        printf '%s\n' $files ;;
    "rev-parse --show-toplevel")
        printf '%s\n' "$root" ;;
    *)
        exec "$(command -v git)" "\$@" ;;
esac
EOF
    chmod +x "$TMPDIR_TEST/git"
}

# Writes a mock clang-format at $1 that records its arguments to $2 (truncated).
make_recording_bin() {
    dest="$1"
    calls="$2"
    mkdir -p "$(dirname "$dest")"
    cat > "$dest" << EOF
#!/usr/bin/env sh
echo "\$*" >> "$calls"
EOF
    chmod +x "$dest"
    : > "$calls"
}

# Writes a mock clang-format on PATH (in $TMPDIR_TEST) that records its arguments.
setup_clang_format_mock() {
    make_recording_bin "$TMPDIR_TEST/clang-format" "$CALLS_FILE"
}

# Runs the hook with the PATH mock in front and no CFORMAT_BIN override.
run_hook() {
    PATH="$TMPDIR_TEST:$PATH" CFORMAT_BIN= sh "$HOOK" 2>&1
}

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

# ── tests: file-extension filtering (falls back to PATH mock) ─────────────────

setup_clang_format_mock

# .hpp is processed (this was the regression — old hook only matched .cpp)
mock_staged "clock.hpp"
: > "$CALLS_FILE"
run_hook > /dev/null
check ".hpp files are formatted" "grep -qF 'clock.hpp' '$CALLS_FILE'"

# .cpp is processed
mock_staged "sample.cpp"
: > "$CALLS_FILE"
run_hook > /dev/null
check ".cpp files are formatted" "grep -qF 'sample.cpp' '$CALLS_FILE'"

# .h is processed
mock_staged "sample.h"
: > "$CALLS_FILE"
run_hook > /dev/null
check ".h files are formatted" "grep -qF 'sample.h' '$CALLS_FILE'"

# .cc is processed
mock_staged "frame.cc"
: > "$CALLS_FILE"
run_hook > /dev/null
check ".cc files are formatted" "grep -qF 'frame.cc' '$CALLS_FILE'"

# .py is skipped
mock_staged "test_lock.py"
: > "$CALLS_FILE"
run_hook > /dev/null
check ".py files are not formatted" "! grep -qF 'test_lock.py' '$CALLS_FILE'"

# .pyx is skipped
mock_staged "_lock.pyx"
: > "$CALLS_FILE"
run_hook > /dev/null
check ".pyx files are not formatted" "! grep -qF '_lock.pyx' '$CALLS_FILE'"

# Mixed staged files: only C/C++ are formatted
mock_staged "clock.hpp _lock.pyx sample.cpp test.py frame.cc"
: > "$CALLS_FILE"
run_hook > /dev/null
check "mixed: .hpp formatted"    "grep -qF 'clock.hpp'  '$CALLS_FILE'"
check "mixed: .cpp formatted"    "grep -qF 'sample.cpp' '$CALLS_FILE'"
check "mixed: .cc formatted"     "grep -qF 'frame.cc'   '$CALLS_FILE'"
check "mixed: .pyx not formatted" "! grep -qF '_lock.pyx' '$CALLS_FILE'"
check "mixed: .py not formatted"  "! grep -qF 'test.py'   '$CALLS_FILE'"

# No staged files: hook prints skip message and does not call clang-format
mock_staged ""
: > "$CALLS_FILE"
output=$(run_hook)
check "no staged files: clang-format not called" "! [ -s '$CALLS_FILE' ]"
check "no staged files: skip message printed"    "echo '$output' | grep -q 'skipped'"

# ── tests: binary resolution order (CFORMAT_BIN > .venv-lint > PATH) ──────────

# CFORMAT_BIN override takes precedence over the PATH binary
setup_clang_format_mock
CFBIN="$TMPDIR_TEST/custom-clang-format"
CFBIN_CALLS="$TMPDIR_TEST/cfbin-calls.txt"
make_recording_bin "$CFBIN" "$CFBIN_CALLS"
mock_staged "override.cpp"
PATH="$TMPDIR_TEST:$PATH" CFORMAT_BIN="$CFBIN" sh "$HOOK" > /dev/null 2>&1
check "CFORMAT_BIN override is used"        "grep -qF 'override.cpp' '$CFBIN_CALLS'"
check "CFORMAT_BIN override bypasses PATH"  "! grep -qF 'override.cpp' '$CALLS_FILE'"

# The pinned .venv-lint binary is preferred over PATH when CFORMAT_BIN is unset
setup_clang_format_mock
VENVROOT="$TMPDIR_TEST/venvroot"
VENV_CALLS="$TMPDIR_TEST/venv-calls.txt"
make_recording_bin "$VENVROOT/.venv-lint/bin/clang-format" "$VENV_CALLS"
mock_staged "pinned.cpp" "$VENVROOT"
PATH="$TMPDIR_TEST:$PATH" CFORMAT_BIN= sh "$HOOK" > /dev/null 2>&1
check ".venv-lint binary is preferred"         "grep -qF 'pinned.cpp' '$VENV_CALLS'"
check ".venv-lint preferred over PATH binary"  "! grep -qF 'pinned.cpp' '$CALLS_FILE'"

# Falls back to PATH clang-format with a drift warning when no override / no .venv-lint
setup_clang_format_mock
mock_staged "fallback.cpp" "$TMPDIR_TEST"
OUT_FILE="$TMPDIR_TEST/hook-out.txt"
PATH="$TMPDIR_TEST:$PATH" CFORMAT_BIN= sh "$HOOK" > "$OUT_FILE" 2>&1
check "fallback uses PATH clang-format" "grep -qF 'fallback.cpp' '$CALLS_FILE'"
check "fallback prints drift warning"   "grep -q 'may differ from the version CI enforces' '$OUT_FILE'"

# ── summary ──────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
