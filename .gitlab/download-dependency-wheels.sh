#!/bin/bash
set -eo pipefail

PYTHON_EXE=${1:-python3}

if [ -z "$CI_COMMIT_SHA" ]; then
  echo "Error: CI_COMMIT_SHA was not provided"
  exit 1
fi

$PYTHON_EXE -m pip install -U "pip>=22.0"
$PYTHON_EXE -m pip install packaging

mkdir wheelhouse-dep

cd wheelhouse

export PYTHONUNBUFFERED=TRUE

../lib-injection/dl_wheels.py \
    --python-version=$PYTHON_VERSION \
    --local-ddtrace \
    --arch $ARCH \
    --platform $PLATFORM \
    --output-dir ../wheelhouse-dep \
    --verbose
