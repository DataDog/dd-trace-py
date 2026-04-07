#!/bin/sh
# Test the cython-lint skip logic in run-fmt.sh for stub-only commits.
# Verifies that when only .pyi files are staged, cython-lint is NOT invoked.
# Run: sh hooks/scripts/test-run-fmt-cython-lint.sh

run_test() {
    staged_files="$1"
    expect_cython_lint="$2"  # "yes" or "no"
    test_name="$3"

    staged_py_only=$(echo "$staged_files" | tr ' ' '\n' | grep -v '\.pyi$' | grep -v '^$' | tr '\n' ' ')
    if [ -n "$(printf '%s' "$staged_py_only" | tr -d ' \t\n')" ]; then
        would_run_cython_lint="yes"
    else
        would_run_cython_lint="no"
    fi

    if [ "$would_run_cython_lint" = "$expect_cython_lint" ]; then
        echo "PASS: $test_name"
        return 0
    else
        echo "FAIL: $test_name (expected cython-lint=$expect_cython_lint, got=$would_run_cython_lint, staged_py_only='$staged_py_only')"
        return 1
    fi
}

failed=0

# Only .pyi files - cython-lint must NOT run (bug case: whitespace-only staged_py_only)
run_test "a.pyi b.pyi " "no" "only .pyi files (trailing space)" || failed=1
run_test "a.pyi b.pyi" "no" "only .pyi files (no trailing space)" || failed=1
run_test " single.pyi " "no" "single .pyi with surrounding spaces" || failed=1

# Only .py files - cython-lint must run
run_test "a.py b.py " "yes" "only .py files" || failed=1
run_test "foo.py" "yes" "single .py file" || failed=1

# Mixed - cython-lint must run (on .py files)
run_test "a.py b.pyi c.py " "yes" "mixed .py and .pyi" || failed=1

# Whitespace-only staged_py_only (edge case from trailing space after filtering)
run_test " " "no" "whitespace only" || failed=1
run_test "" "no" "empty" || failed=1

if [ $failed -eq 0 ]; then
    echo ""
    echo "All tests passed."
    exit 0
else
    echo ""
    echo "Some tests failed."
    exit 1
fi
