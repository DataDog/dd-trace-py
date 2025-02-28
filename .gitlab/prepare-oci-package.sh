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

echo "Cleaning up binaries for ${ARCH}"
if [ "${ARCH}" == "arm64" ]; then
  echo "Removing x86_64 binaries"
  find ../pywheels-dep/ -type f -name '*x86_64*' -exec rm -f {} \;
elif [ "${ARCH}" == "amd64" ]; then
  echo "Removing aarch64 binaries"
  find ../pywheels-dep/ -type f -name '*aarch64*' -exec rm -f {} \;
else
  echo "No ARCH set, not removing any binaries"
fi
cp -r ../pywheels-dep/site-packages* sources/ddtrace_pkgs

cp ../lib-injection/sources/* sources/

if ! type rdfind &> /dev/null; then
  clean-apt install rdfind
fi
echo "Deduplicating package files"
cd ./sources
rdfind -makesymlinks true -makeresultsfile true -checksum sha256 -deterministic true -outputname deduped.txt .
echo "Converting symlinks to relative symlinks"
find . -type l | while read -r l; do
  target="$(realpath "$l")"
  rel_target="$(realpath --relative-to="$(dirname "$(realpath -s "$l")")" "$target")"
  dest_base="$(basename "$l")"
  dest_dir="$(dirname "$l")"
  (cd "${dest_dir}" && ln -sf "${rel_target}" "${dest_base}")
done
