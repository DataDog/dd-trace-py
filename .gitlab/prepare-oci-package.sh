#!/bin/bash

if [ -n "$CI_COMMIT_TAG" ] && [ -z "$PYTHON_PACKAGE_VERSION" ]; then
  PYTHON_PACKAGE_VERSION=${CI_COMMIT_TAG##v}
fi

if [ -z "$PYTHON_PACKAGE_VERSION" ]; then
  # Get the version from the filename of the first wheel
  # wheels have the form:
  # ddtrace-2.11.0.dev41+g50bf57680-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl
  # the version is everything between the first and second "-"
  WHEELS_LIST=(../pywheels/*.whl)
  FIRST_WHEEL=${WHEELS_LIST[1]}

  #everything before -
  WITHOUT_BEGINNING=${FIRST_WHEEL#*-}

  #strip after -
  PYTHON_PACKAGE_VERSION=${WITHOUT_BEGINNING%%-*}
fi

mkdir -p sources/ddtrace_pkgs

BUILD_DIR=sources

echo -n "$PYTHON_PACKAGE_VERSION" > sources/version

cp -r ../pywheels-dep/site-packages* sources/ddtrace_pkgs

cp ../lib-injection/sitecustomize.py sources/
cp ../min_compatible_versions.csv sources/
cp ../lib-injection/telemetry-forwarder.sh sources/

clean-apt install python3
echo "Deduplicating package files"
python3 ../lib-injection/dedupe.py sources/ddtrace_pkgs/
