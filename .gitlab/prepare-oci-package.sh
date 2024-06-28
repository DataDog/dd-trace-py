#!/bin/bash

if [ -n "$CI_COMMIT_TAG" ] && [ -z "$PYTHON_PACKAGE_VERSION" ]; then
  PYTHON_PACKAGE_VERSION=${CI_COMMIT_TAG##v}
fi

if [ -z "$PYTHON_PACKAGE_VERSION" ]; then
  # Get the version from the filename of the first wheel
  # wheels have the form:
  # ddtrace-2.11.0.dev41+g50bf57680-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.wh
  # the version is everything between the first and second "-"
  WHEELS_LIST=(../pywheels/*.whl)
  FIRST_WHEEL=${WHEELS_LIST[1]}

  #everything before -
  WITHOUT_BEGINNING=${FIRST_WHEEL#*-}

  #strip after -
  PYTHON_PACKAGE_VERSION=${WITHOUT_BEGINNING%%-*}
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

../.gitlab/unpack_wheels.py \
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
