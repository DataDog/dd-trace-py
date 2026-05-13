#!/usr/bin/env sh
# Tests for hooks/scripts/run-fmt.sh
#
# Stubs git and hatch so no real repo or toolchain is needed.
# Run with:  sh hooks/tests/test-run-fmt.sh

set -eu

SCRIPT="$(cd "$(dirname "$0")/../scripts" && pwd)/run-fmt.sh"
PASS=0
FAIL=0

# ── helpers ──────────────────────────────────────────────────────────────────

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

HATCH_CALLS="$TMPDIR_TEST/hatch-calls.txt"
GIT_ADD_CALLS="$TMPDIR_TEST/git-add-calls.txt"

# Writes a mock git that emits $1 (space-separated filenames) for the staged
# query, nothing for the unstaged query, and records `git add` calls.
mock_staged() {
    files="$1"
    # Convert space-separated to newline-separated for the grep output
    files_nl=$(printf '%s' "$files" | tr ' ' '\n')
    cat > "$TMPDIR_TEST/git" << EOF
#!/usr/bin/env sh
case "\$*" in
    "diff --staged --name-only HEAD --diff-filter=ACMR")
        printf '%s\n' $files ;;
    "diff --name-only")
        ;;  # no unstaged changes
    add\ *)
        echo "\$*" >> "$GIT_ADD_CALLS" ;;
    *)
        exec "$(command -v git)" "\$@" ;;
esac
EOF
    chmod +x "$TMPDIR_TEST/git"
}

setup_hatch_mock() {
    cat > "$TMPDIR_TEST/hatch" << EOF
#!/usr/bin/env sh
echo "\$*" >> "$HATCH_CALLS"
EOF
    chmod +x "$TMPDIR_TEST/hatch"
    : > "$HATCH_CALLS"
    : > "$GIT_ADD_CALLS"
}

run_hook() {
    PATH="$TMPDIR_TEST:$PATH" sh "$SCRIPT" 2>&1
}

hatch_was_called_with() {
    grep -qF "$1" "$HATCH_CALLS"
}

check() {
    name="$1"; condition="$2"
    if eval "$condition"; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name  (hatch calls: $(cat "$HATCH_CALLS" 2>/dev/null || echo '<none>'))"
        FAIL=$((FAIL + 1))
    fi
}

# ── tests ────────────────────────────────────────────────────────────────────

setup_hatch_mock

# .py files: ruff runs, cython-lint does NOT (ruff already covers pycodestyle for .py)
mock_staged "foo.py"
: > "$HATCH_CALLS"
run_hook > /dev/null
check ".py: ruff format runs"        "hatch_was_called_with 'lint:ruff format'"
check ".py: ruff check runs"         "hatch_was_called_with 'lint:ruff check'"
check ".py: cython-lint skipped"     "! hatch_was_called_with 'lint:cython-lint'"

# .pyx files: cython-lint runs, ruff does NOT (ruff doesn't support Cython syntax)
# See https://github.com/astral-sh/ruff/issues/10250
mock_staged "_lock.pyx"
: > "$HATCH_CALLS"
run_hook > /dev/null
check ".pyx: ruff format skipped" "! hatch_was_called_with 'lint:ruff format'"
check ".pyx: ruff check skipped"  "! hatch_was_called_with 'lint:ruff check'"
check ".pyx: cython-lint runs"    "hatch_was_called_with 'lint:cython-lint'"

# .pyi stubs: ruff runs, cython-lint does NOT (compact stub format conflicts)
mock_staged "_ddup.pyi"
: > "$HATCH_CALLS"
run_hook > /dev/null
check ".pyi: ruff format runs"      "hatch_was_called_with 'lint:ruff format'"
check ".pyi: ruff check runs"       "hatch_was_called_with 'lint:ruff check'"
check ".pyi: cython-lint skipped"   "! hatch_was_called_with 'lint:cython-lint'"

# .cpp files are not processed at all
mock_staged "sample.cpp"
: > "$HATCH_CALLS"
run_hook > /dev/null
check ".cpp: no hatch calls"     "! [ -s '$HATCH_CALLS' ]"

# Mixed staged files: each tool only sees the right files
mock_staged "foo.py bar.pyx stub.pyi sample.cpp"
: > "$HATCH_CALLS"
run_hook > /dev/null
check "mixed: ruff format runs"             "hatch_was_called_with 'lint:ruff format'"
check "mixed: ruff check runs"              "hatch_was_called_with 'lint:ruff check'"
check "mixed: ruff sees .py"                "grep 'ruff format' '$HATCH_CALLS' | grep -q 'foo.py'"
check "mixed: ruff sees .pyi"               "grep 'ruff format' '$HATCH_CALLS' | grep -q 'stub.pyi'"
check "mixed: ruff skips .pyx"              "! grep 'ruff' '$HATCH_CALLS' | grep -q 'bar.pyx'"
check "mixed: cython-lint runs"             "hatch_was_called_with 'lint:cython-lint'"
check "mixed: cython-lint sees .pyx"        "grep 'cython-lint' '$HATCH_CALLS' | grep -q 'bar.pyx'"
check "mixed: cython-lint skips .py"        "! grep 'cython-lint' '$HATCH_CALLS' | grep -q 'foo.py'"
check "mixed: cython-lint skips .pyi"       "! grep 'cython-lint' '$HATCH_CALLS' | grep -q 'stub.pyi'"
check "mixed: .cpp not passed to hatch"     "! hatch_was_called_with 'sample.cpp'"

# No staged files: hook prints skip message and does not invoke hatch
mock_staged ""
: > "$HATCH_CALLS"
output=$(run_hook)
check "no staged files: hatch not called"    "! [ -s '$HATCH_CALLS' ]"
check "no staged files: skip message printed" "printf '%s' '$output' | grep -q 'skipped'"

# ── summary ──────────────────────────────────────────────────────────────────

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
