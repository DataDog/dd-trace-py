#!/bin/bash

if [ -n "$CI_COMMIT_TAG" ] && [ -z "$PYTHON_PACKAGE_VERSION" ]; then
  PYTHON_PACKAGE_VERSION=${CI_COMMIT_TAG##v}
fi

mkdir -p sources

BUILD_DIR=sources

echo -n "$PYTHON_PACKAGE_VERSION" > sources/version

# Install known compatible pip as default version shipped in Ubuntu (20.0.2)
# does not work.
python3 -m pip install -U "pip>=22.0"
python3 -m pip install packaging

WHEEL_ARCH="x86_64"
if [ "$ARCH" = "arm64" ]; then
  WHEEL_ARCH="aarch64"
fi

unpack_wheels.py \
    --python-version=3.12 \
    --python-version=3.11 \
    --python-version=3.10 \
    --python-version=3.9 \
    --python-version=3.8 \
    --python-version=3.7 \
    --arch=x86_64 \
    --platform=musllinux_1_1 \
    --platform=manylinux2014 \
    --input-dir=../pywheels \
    --output-dir=../sources/ddtrace_pkgs \
    --verbose

unpack_wheels.py \
    --python-version=3.12 \
    --python-version=3.11 \
    --python-version=3.10 \
    --python-version=3.9 \
    --python-version=3.8 \
    --python-version=3.7 \
    --arch=$WHEEL_ARCH \
    --platform=musllinux_1_1 \
    --platform=manylinux2014 \
    --input-dir=../pywheels \
    --output-dir=$BUILD_DIR/ddtrace_pkgs \
    --verbose

cp ../lib-injection/sitecustomize.py $BUILD_DIR/
cp ../min_compatible_versions.csv $BUILD_DIR/
cp ../lib-injection/telemetry-forwarder.sh $BUILD_DIR/
