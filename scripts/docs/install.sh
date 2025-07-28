#!/usr/bin/env bash

set -ex

if [[ "${READTHEDOCS}" = "True" ]]; then
    # We skip here because we do not check spelling in RTD
    echo "Skipping install"
elif [[ "$(uname)" == "Darwin" ]]; then
  brew install enchant

# libenchant is already installed in our base dev image docker/Dockerfile
fi
