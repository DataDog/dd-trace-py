#!/bin/bash
set -eo pipefail

if [ "$OS" != "linux" ]; then
  echo "Only linux packages are supported. Exiting"
  exit 0
fi

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

case "${ARCH}" in
  amd64) WHEEL_ARCH=x86_64 ;;
  arm64) WHEEL_ARCH=aarch64 ;;
  *) echo "Unsupported ARCH: ${ARCH}" >&2; exit 1 ;;
esac

# pip download selects wheels by --python-version and --abi, so one interpreter
# fetches the dependencies for every Python version that the package supports.
mkdir -p ../pywheels-dep
(
  cd ../pywheels
  PYTHONUNBUFFERED=TRUE python3 ../lib-injection/dl_wheels.py \
    --python-version=3.9 \
    --python-version=3.10 \
    --python-version=3.11 \
    --python-version=3.12 \
    --python-version=3.13 \
    --python-version=3.14 \
    --python-version=3.15 \
    --ddtrace-flavor="${DDTRACE_FLAVOR:-}" \
    --local-ddtrace \
    --arch "${WHEEL_ARCH}" \
    --platform musllinux_1_2 \
    --platform manylinux2014 \
    --output-dir ../pywheels-dep \
    --verbose
)
cp -r ../pywheels-dep/site-packages* sources/ddtrace_pkgs

cp ../lib-injection/sources/* sources/

if ! type rdfind &> /dev/null; then
  for attempt in 1 2 3; do
    if clean-apt install rdfind; then
      break
    fi
    if [ "$attempt" -eq 3 ]; then
      echo "clean-apt install rdfind failed after 3 attempts" >&2
      exit 1
    fi
    echo "clean-apt install rdfind failed (attempt $attempt/3), retrying in 5s..."
    sleep 5
  done
fi
echo "Deduplicating package files"
cd ./sources
rdfind -makesymlinks true -makeresultsfile true -checksum sha256 -deterministic true -outputname deduped.txt .
echo "Converting symlinks to relative symlinks"
python3 ../../.gitlab/scripts/make-symlinks-relative.py .
