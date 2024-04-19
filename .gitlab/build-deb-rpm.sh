#!/bin/bash

if [ -n "$CI_COMMIT_TAG" ] && [ -z "$PYTHON_PACKAGE_VERSION" ]; then
  PYTHON_PACKAGE_VERSION=${CI_COMMIT_TAG##v}
fi

echo -n $PYTHON_PACKAGE_VERSION > auto_inject-python.version

source common_build_functions.sh

mkdir -p dd-trace.dir/lib

# Install known compatible pip as default version shipped in Ubuntu (20.0.2)
# does not work.
python3 -m pip install -U "pip>=22.0"
python3 -m pip install packaging

echo `pwd`

../lib-injection/dl_wheels.py \
    --python-version=3.12 \
    --python-version=3.11 \
    --python-version=3.10 \
    --python-version=3.9 \
    --python-version=3.8 \
    --python-version=3.7 \
    --ddtrace-version=$PYTHON_PACKAGE_VERSION  \
    --arch x86_64 \
    --arch aarch64 \
    --platform musllinux_1_1 \
    --platform manylinux2014 \
    --output-dir dd-trace.dir/lib/ddtrace_pkgs \
    --verbose

cp ../lib-injection/sitecustomize.py dd-trace.dir/lib/

chmod -R o-w dd-trace.dir/lib
chmod -R g-w dd-trace.dir/lib

cp auto_inject-python.version dd-trace.dir/lib/version

fpm_wrapper "datadog-apm-library-python" "$PYTHON_PACKAGE_VERSION" \
  --input-type dir \
  --url "https://github.com/DataDog/dd-trace-py" \
  --description "Datadog APM client library for python" \
  --license "BSD-3-Clause" \
  --chdir=dd-trace.dir/lib \
  --prefix "$LIBRARIES_INSTALL_BASE/python" \
  .=.
