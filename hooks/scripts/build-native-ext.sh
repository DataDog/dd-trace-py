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

"$PYTHON" setup.py build_ext --inplace
