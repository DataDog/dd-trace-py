#!/usr/bin/env bash
set -eux

# Normalize READTHEDOCS: lowercase and strip whitespace to match "true" or "1"
rtd_val="${READTHEDOCS:-}"; rtd_val="${rtd_val,,}"; rtd_val="${rtd_val// /}"
if [[ "$rtd_val" == "true" || "$rtd_val" == "1" ]]; then
  echo "Skipping spelling check in RTD"
else
  if [[ "$(uname)" == "Darwin" ]]; then
    brew install enchant
    export PYENCHANT_LIBRARY_PATH=$(brew --prefix enchant)/lib/libenchant-2.dylib
  fi
  sphinx-build -vvv -W -b spelling docs docs/_build/spelling || (cat docs/_build/spelling/*.spelling && exit 1)
fi
reno lint
sphinx-build -vvv -W -b html docs docs/_build/html
