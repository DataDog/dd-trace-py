#!/bin/bash
set -eo pipefail

if [ -z "$CI_COMMIT_SHA" ]; then
  echo "Error: CI_COMMIT_SHA was not provided"
  exit 1
fi

python3 -m pip install -U "pip>=22.0"
python3 -m pip install packaging

mkdir pywheels-dep

cd pywheels

export PYTHONUNBUFFERED=TRUE

../lib-injection/dl_wheels.py \
    --python-version=$PYTHON_VERSION \
    --local-ddtrace \
    --arch x86_64 \
    --arch aarch64 \
    --platform musllinux_1_2 \
    --platform manylinux2014 \
    --output-dir ../pywheels-dep \
    --verbose
