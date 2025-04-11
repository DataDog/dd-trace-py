#!/usr/bin/env bash
set -eux

# DEV: unless it's built with editable, following sphinx-build fails
CMAKE_BUILD_PARALLEL_LEVEL=12 CARGO_BUILD_JOBS=12 pip install -v -e .

if [[ "$(uname)" == "Darwin" ]]; then
  export PYENCHANT_LIBRARY_PATH=/opt/homebrew/lib/libenchant-2.dylib
fi

reno lint
sphinx-build -vvv -W -b spelling docs docs/_build/html
sphinx-build -vvv -W -b html docs docs/_build/html
