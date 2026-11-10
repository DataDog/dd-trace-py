#!/usr/bin/env bash
set -eux

# Fetch once for both Sphinx builds, retrying transient failures and partial downloads.
mkdir -p docs/_build
curl --fail --silent --show-error --location \
  --retry 5 --retry-delay 2 --retry-connrefused --retry-all-errors \
  --connect-timeout 10 --max-time 60 \
  --output docs/_build/python3.inv https://docs.python.org/3/objects.inv

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
python scripts/reno_compat.py lint
sphinx-build -vvv -W -b html docs docs/_build/html
