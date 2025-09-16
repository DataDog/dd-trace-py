#!/usr/bin/env bash
set -ex -o pipefail

project_dir="${CI_PROJECT_DIR:-.}"

python_bin="${1:-python}"
wheelhouse="${2:-wheelhouse/}"

# If cargo is not on the PATH, update PATH with a default cargo path
if ! command -v cargo &> /dev/null; then
    export PATH="${PATH}:${HOME}/.cargo/bin"
fi

"${python_bin}" -m pip install -U -r ".gitlab/package/requirements-${PYTHON_TAG}.txt"
"${python_bin}" -m pip wheel "${project_dir}" --no-deps --wheel-dir "${wheelhouse}"
"${python_bin}" -m twine check --strict "${wheelhouse}/*"

