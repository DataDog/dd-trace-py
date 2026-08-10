#!/usr/bin/env sh
# Tests for hooks/scripts/run-fmt.sh
#
# Stubs git and the lint runner (via LINT_CMD) so no real repo or toolchain is needed.
# Run with:  sh hooks/tests/test-run-fmt.sh

set -eu

SCRIPT="$(cd "$(dirname "$0")/../scripts" && pwd)/run-fmt.sh"
PASS=0
FAIL=0

# ── helpers ──────────────────────────────────────────────────────────────────

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

LINT_CALLS="$TMPDIR_TEST/lint-calls.txt"
GIT_ADD_CALLS="$TMPDIR_TEST/git-add-calls.txt"

# Writes a mock git that emits $1 (space-separated filenames) for the staged
# query, nothing for the unstaged query, and records `git add` calls.
mock_staged() {
    files="$1"
    cat > "$TMPDIR_TEST/git" << EOF
#!/usr/bin/env sh
case "\$*" in
    "diff --staged --name-only HEAD --diff-filter=ACMR")
        printf '%s\n' $files ;;
    "diff --name-only")
        ;;  # no unstaged changes
    "diff --name-only --diff-filter=ACMR")
        ;;  # no dirty files to check formatting on
    add\ *)
        echo "\$*" >> "$GIT_ADD_CALLS" ;;
    *)
        exec "$(command -v git)" "\$@" ;;
esac
EOF
    chmod +x "$TMPDIR_TEST/git"
}

setup_lint_mock() {
    cat > "$TMPDIR_TEST/lint-mock" << EOF
#!/usr/bin/env sh
echo "\$*" >> "$LINT_CALLS"
EOF
    chmod +x "$TMPDIR_TEST/lint-mock"
    : > "$LINT_CALLS"
    : > "$GIT_ADD_CALLS"
}

run_hook() {
    PATH="$TMPDIR_TEST:$PATH" LINT_CMD="$TMPDIR_TEST/lint-mock" sh "$SCRIPT" 2>&1
}

lint_was_called_with() {
    grep -qF "$1" "$LINT_CALLS"
}

check() {
    name="$1"; condition="$2"
    if eval "$condition"; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name  (lint calls: $(cat "$LINT_CALLS" 2>/dev/null || echo '<none>'))"
        FAIL=$((FAIL + 1))
    fi
}

# ── tests ────────────────────────────────────────────────────────────────────

setup_lint_mock

# .py files: ruff runs, cython-lint does NOT (ruff already covers pycodestyle for .py)
mock_staged "foo.py"
: > "$LINT_CALLS"
run_hook > /dev/null
check ".py: ruff format runs"        "lint_was_called_with 'ruff format'"
check ".py: ruff check runs"         "lint_was_called_with 'ruff check'"
check ".py: cython-lint skipped"     "! lint_was_called_with 'cython-lint'"

# .pyx files: cython-lint runs, ruff does NOT (ruff doesn't support Cython syntax)
# See https://github.com/astral-sh/ruff/issues/10250
mock_staged "_lock.pyx"
: > "$LINT_CALLS"
run_hook > /dev/null
check ".pyx: ruff format skipped" "! lint_was_called_with 'ruff format'"
check ".pyx: ruff check skipped"  "! lint_was_called_with 'ruff check'"
check ".pyx: cython-lint runs"    "lint_was_called_with 'cython-lint'"

# .pyi stubs: ruff runs, cython-lint does NOT (compact stub format conflicts)
mock_staged "_ddup.pyi"
: > "$LINT_CALLS"
run_hook > /dev/null
check ".pyi: ruff format runs"      "lint_was_called_with 'ruff format'"
check ".pyi: ruff check runs"       "lint_was_called_with 'ruff check'"
check ".pyi: cython-lint skipped"   "! lint_was_called_with 'cython-lint'"

# .cpp files are not processed at all
mock_staged "sample.cpp"
: > "$LINT_CALLS"
run_hook > /dev/null
check ".cpp: no lint calls"     "! [ -s '$LINT_CALLS' ]"

# Mixed staged files: each tool only sees the right files
mock_staged "foo.py bar.pyx stub.pyi sample.cpp"
: > "$LINT_CALLS"
run_hook > /dev/null
check "mixed: ruff format runs"             "lint_was_called_with 'ruff format'"
check "mixed: ruff check runs"              "lint_was_called_with 'ruff check'"
check "mixed: ruff sees .py"                "grep 'ruff format' '$LINT_CALLS' | grep -q 'foo.py'"
check "mixed: ruff sees .pyi"               "grep 'ruff format' '$LINT_CALLS' | grep -q 'stub.pyi'"
check "mixed: ruff skips .pyx"              "! grep 'ruff' '$LINT_CALLS' | grep -q 'bar.pyx'"
check "mixed: cython-lint runs"             "lint_was_called_with 'cython-lint'"
check "mixed: cython-lint sees .pyx"        "grep 'cython-lint' '$LINT_CALLS' | grep -q 'bar.pyx'"
check "mixed: cython-lint skips .py"        "! grep 'cython-lint' '$LINT_CALLS' | grep -q 'foo.py'"
check "mixed: cython-lint skips .pyi"       "! grep 'cython-lint' '$LINT_CALLS' | grep -q 'stub.pyi'"
check "mixed: .cpp not passed to lint"      "! lint_was_called_with 'sample.cpp'"

# No staged files: hook prints skip message and does not invoke lint runner
mock_staged ""
: > "$LINT_CALLS"
output=$(run_hook)
check "no staged files: lint not called"      "! [ -s '$LINT_CALLS' ]"
check "no staged files: skip message printed" "printf '%s' '$output' | grep -q 'skipped'"

# ── summary ──────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
