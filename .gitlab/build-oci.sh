#!/bin/bash

source common_build_functions.sh

if [ -n "$CI_COMMIT_TAG" ] && [ -z "$PYTHON_PACKAGE_VERSION" ]; then
  PYTHON_PACKAGE_VERSION=${CI_COMMIT_TAG##v}
fi

if [ -z "$ARCH" ]; then
  ARCH=amd64
fi


TMP_DIR=$(mktemp --dir)
BUILD_DIR=$TMP_DIR/datadog-python-apm.build
mkdir $TMP_DIR/datadog-python-apm.build

# Install known compatible pip as default version shipped in Ubuntu (20.0.2)
# does not work.
python3 -m pip install -U "pip>=22.0"
python3 -m pip install packaging

WHEEL_ARCH="x86_64"
if [ "$ARCH" = "arm64" ]; then
  WHEEL_ARCH="aarch64"
fi

../lib-injection/dl_wheels.py \
    --python-version=3.12 \
    --python-version=3.11 \
    --python-version=3.10 \
    --python-version=3.9 \
    --python-version=3.8 \
    --python-version=3.7 \
    --ddtrace-version=$PYTHON_PACKAGE_VERSION \
    --arch=$WHEEL_ARCH \
    --platform=musllinux_1_1 \
    --platform=manylinux2014 \
    --output-dir=$BUILD_DIR/ddtrace_pkgs \
    --verbose
echo -n $PYTHON_PACKAGE_VERSION > auto_inject-python.version
cp ../lib-injection/sitecustomize.py $BUILD_DIR/
cp auto_inject-python.version $BUILD_DIR/version
cp ../min_compatible_versions.csv $BUILD_DIR/
cp ../lib-injection/telemetry-forwarder.sh $BUILD_DIR/
chmod -R o-w $BUILD_DIR
chmod -R g-w $BUILD_DIR

# Build packages
datadog-package create \
    --version="$PYTHON_PACKAGE_VERSION" \
    --package="datadog-apm-library-python" \
    --archive=true \
    --archive-path="datadog-apm-library-python-$PYTHON_PACKAGE_VERSION-$ARCH.tar" \
    --arch "$ARCH" \
    --os "linux" \
    $BUILD_DIR
