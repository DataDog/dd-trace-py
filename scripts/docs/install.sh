#!/usr/bin/env bash

set -ex

if [[ "${READTHEDOCS}" = "True" ]]; then
    # We skip here because we do not check spelling in RTD
    echo "Skipping install"
else
  if [[ "$(uname)" == "Darwin" ]]; then
    brew install enchant
  fi
fi
