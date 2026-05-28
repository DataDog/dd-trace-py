#!/usr/bin/env sh
# Remind or rebuild in-tree native extensions when staged changes touch product native/build inputs.
#
# Default: warn only (does not block the commit).
# Blocking rebuild:  export DD_BUILD_NATIVE_ON_COMMIT=1
# Skip entirely:     export DD_SKIP_NATIVE_BUILD=1  or  git commit --no-verify

set -eu

NATIVE_FILE_PATTERN='\.(c|h|cc|cpp|hpp|rs|pyx|pxd)$|CMakeLists\.txt|\.cmake$|^setup\.py$'
# Product paths only; skip test harness / fuzz trees (still covered by clang-format etc.).
TEST_PATH_EXCLUDE='(^|/)(test|tests|fuzz)(/|$)|/dd_wrapper/test/|_test\.cpp$'

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

if [ "${DD_SKIP_NATIVE_BUILD:-}" = "1" ]; then
    echo 'native build hook skipped: DD_SKIP_NATIVE_BUILD=1'
    exit 0
fi

staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
if [ -z "$staged_files" ]; then
    echo 'native build hook skipped: no staged files'
    exit 0
fi

product_native_files=$(printf '%s\n' "$staged_files" \
    | grep -E "$NATIVE_FILE_PATTERN" \
    | grep -vE "$TEST_PATH_EXCLUDE" || true)

if [ -z "$product_native_files" ]; then
    echo 'native build hook skipped: no staged product native or build files'
    exit 0
fi

resolve_python() {
    if [ -x .venv/bin/python ]; then
        PYTHON=.venv/bin/python
    elif [ -n "${PYTHON:-}" ]; then
        PYTHON=$PYTHON
    else
        PYTHON=python3
    fi
}

warn_staged_native() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "⚠️  STAGED NATIVE/BUILD FILES (rebuild recommended)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "The following staged files may require rebuilt native extensions:"
    echo ""

    printf '%s\n' "$product_native_files" | head -n 10 | sed 's/^/  • /'

    total=$(printf '%s\n' "$product_native_files" | wc -l | tr -d ' ')
    if [ "$total" -gt 10 ]; then
        echo "  ... and $((total - 10)) more file(s)"
    fi

    echo ""
    echo "You may need to rebuild native extensions before running or pushing."
    echo ""
    echo "  # In-place rebuild (fastest when already installed editable):"
    echo "    python setup.py build_ext --inplace"
    echo ""
    echo "  # Or reinstall editable:"
    echo "    pip install -e ."
    echo ""
    echo "  # Block commit until build succeeds (this session):"
    echo "    export DD_BUILD_NATIVE_ON_COMMIT=1"
    echo ""
    echo "post-merge/post-checkout hooks also remind you after pull or branch switch."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

is_stale_eggs_error() {
    printf '%s\n' "$1" | grep -qiE 'directory not empty|errno 66|\[errno 66\]'
}

run_build_ext() {
    set +e
    build_output=$("$PYTHON" setup.py build_ext --inplace 2>&1)
    build_status=$?
    set -e
    return "$build_status"
}

run_blocking_build() {
    resolve_python
    echo "Rebuilding native extensions ($PYTHON setup.py build_ext --inplace)..."
    echo "Triggered by staged product native/build files:"
    printf '%s\n' "$product_native_files" | sed 's/^/  /'
    echo ""

    if run_build_ext; then
        printf '%s\n' "$build_output"
        return 0
    fi

    printf '%s\n' "$build_output" >&2

    if is_stale_eggs_error "$build_output" && [ -d .eggs ]; then
        echo "" >&2
        echo "build_ext failed with a stale .eggs cache; removing .eggs and retrying once..." >&2
        rm -rf .eggs
        build_output=""
        if run_build_ext; then
            printf '%s\n' "$build_output"
            return 0
        fi
        printf '%s\n' "$build_output" >&2
    fi

    return "$build_status"
}

if [ "${DD_BUILD_NATIVE_ON_COMMIT:-}" = "1" ]; then
    run_blocking_build
    exit $?
fi

warn_staged_native
exit 0
