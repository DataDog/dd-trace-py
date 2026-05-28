#!/usr/bin/env sh
# Rebuild in-tree native extensions when staged changes touch native/build inputs.
#
# Opt out for a single commit:  git commit --no-verify
# Opt out for the shell session:  export DD_SKIP_NATIVE_BUILD=1

set -eu

if [ "${DD_SKIP_NATIVE_BUILD:-}" = "1" ]; then
    echo 'build_ext skipped: DD_SKIP_NATIVE_BUILD=1'
    exit 0
fi

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
if [ -z "$staged_files" ]; then
    echo 'build_ext skipped: no staged files'
    exit 0
fi

if ! printf '%s\n' "$staged_files" | grep -qE \
    '\.(c|h|cc|cpp|hpp|rs|pyx|pxd)$|CMakeLists\.txt|\.cmake$|^setup\.py$'; then
    echo 'build_ext skipped: no staged native or build files'
    exit 0
fi

if [ -x .venv/bin/python ]; then
    PYTHON=.venv/bin/python
elif [ -n "${PYTHON:-}" ]; then
    PYTHON=$PYTHON
else
    PYTHON=python3
fi

echo "Rebuilding native extensions ($PYTHON setup.py build_ext --inplace)..."
echo "Staged native/build files:"
printf '%s\n' "$staged_files" | grep -E \
    '\.(c|h|cc|cpp|hpp|rs|pyx|pxd)$|CMakeLists\.txt|\.cmake$|^setup\.py$' || true
echo ""

<<<<<<< Updated upstream
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

if run_build_ext; then
    printf '%s\n' "$build_output"
    exit 0
fi

printf '%s\n' "$build_output" >&2

if is_stale_eggs_error "$build_output" && [ -d .eggs ]; then
    echo "" >&2
    echo "build_ext failed with a stale .eggs cache; removing .eggs and retrying once..." >&2
    rm -rf .eggs
    build_output=""
    if run_build_ext; then
        printf '%s\n' "$build_output"
        exit 0
    fi
    printf '%s\n' "$build_output" >&2
fi

exit "$build_status"
=======
"$PYTHON" setup.py build_ext --inplace
>>>>>>> Stashed changes
