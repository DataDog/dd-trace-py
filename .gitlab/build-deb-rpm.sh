#!/bin/bash

if [ -n "$CI_COMMIT_TAG" ] && [ -z "$PYTHON_PACKAGE_VERSION" ]; then
  PYTHON_PACKAGE_VERSION=${CI_COMMIT_TAG##v}
fi

echo -n $PYTHON_PACKAGE_VERSION > auto_inject-python.version

source common_build_functions.sh

mkdir -p dd-trace.dir/lib

echo `pwd`

docker build lib-injection \
    --build-arg DDTRACE_PYTHON_VERSION=$PYTHON_PACKAGE_VERSION  \
    --platform linux/amd64 \
    --output type=local,dest=out

mv out/datadog-init/ddtrace_pkgs dd-trace.dir/lib/

cp ../lib-injection/sitecustomize.py dd-trace.dir/lib/

cp auto_inject-python.version dd-trace.dir/lib/version

fpm_wrapper "datadog-apm-library-python" "$PYTHON_PACKAGE_VERSION" \
  --input-type dir \
  --url "https://github.com/DataDog/dd-trace-py" \
  --description "Datadog APM client library for python" \
  --license "BSD-3-Clause" \
  --chdir=dd-trace.dir/lib \
  --prefix "$LIBRARIES_INSTALL_BASE/python" \
  .=.
