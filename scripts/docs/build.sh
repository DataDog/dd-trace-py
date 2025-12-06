#!/usr/bin/env bash
set -eux

if [[ "${READTHEDOCS:-}" = "True" ]]; then
  echo "Skipping spelling check in RTD"
else
  if [[ "$(uname)" == "Darwin" ]]; then
    brew install enchant
    export PYENCHANT_LIBRARY_PATH=$(brew --prefix enchant)/lib/libenchant-2.dylib
  fi
  sphinx-build -vvv -W -b spelling docs docs/_build/spelling
fi
reno lint
sphinx-build -vvv -W -b html docs docs/_build/html
