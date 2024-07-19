#!/bin/bash
set -eo pipefail

if [ -z "$CI_COMMIT_SHA" ]; then
  echo "Error: CI_COMMIT_SHA was not provided"
  exit 1
fi

python3 -m pip install -U "pip>=22.0"
python3 -m pip install packaging

mkdir pywheels-dep

../lib-injection/dl_wheels.py \
    --python-version=3.12 \
    --python-version=3.11 \
    --python-version=3.10 \
    --python-version=3.9 \
    --python-version=3.8 \
    --python-version=3.7 \
    --ddtrace-commit-hash=$CI_COMMIT_SHA  \
    --arch x86_64 \
    --arch aarch64 \
    --platform musllinux_1_1 \
    --platform manylinux2014 \
    --output-dir pywheels-dep \
    --verbose

ls -l pywheels-dep

rm -r ddtrace*

ls -l pywheels-dep
