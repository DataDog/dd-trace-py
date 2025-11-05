#!/usr/bin/env bash
set -eux

if [[ "${READTHEDOCS:-}" = "True" ]]; then
    # We skip here because we do not check spelling in RTD
    echo "Skipping install"
else
  if [[ "$(uname)" == "Darwin" ]]; then
    brew install enchant
  fi
fi

if [[ "$(uname)" == "Darwin" ]]; then
  export PYENCHANT_LIBRARY_PATH=/opt/homebrew/lib/libenchant-2.dylib
fi

reno lint
sphinx-build -vvv -W -b spelling docs docs/_build/html
sphinx-build -vvv -W -b html docs docs/_build/html
