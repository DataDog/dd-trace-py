#!/usr/bin/env bash
set -eux

if [[ "${READTHEDOCS:-}" = "True" ]]; then
  echo "Skipping spelling check in RTD"
else
  if [[ "$(uname)" == "Darwin" ]]; then
    export PYENCHANT_LIBRARY_PATH=/opt/homebrew/lib/libenchant-2.dylib
    brew install enchant
  fi
  sphinx-build -vvv -W -b spelling docs docs/_build/spelling
fi
reno lint
sphinx-build -vvv -W -b html docs docs/_build/html
