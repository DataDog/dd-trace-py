#!/bin/bash
# http://redsymbol.net/articles/unofficial-bash-strict-mode/
set -euox pipefail
IFS=$'\n\t'

usage() {
    echo "Usage :"
    echo "$0 <version> <path>"
    echo ""
    echo "Example"
    echo "  $0 v0.7.0-rc.1 ./vendor"
}

if [ $# != 2 ] || [ "$1" == "-h" ]; then
    usage
    exit 1
fi

SCRIPTPATH=$(readlink -f "$0")
SCRIPTDIR=$(dirname "$SCRIPTPATH")

MARCH=$(uname -m)

TAG_LIBDATADOG=$1
TARGET_EXTRACT=$2

CHECKSUM_FILE=${SCRIPTDIR}/libdatadog_checksums.txt

# Test for musl
MUSL_LIBC=$(ldd /bin/ls | grep 'musl' | head -1 | cut -d ' ' -f1 || true)
if [[ -n ${MUSL_LIBC-""} ]]; then
    DISTRIBUTION="alpine-linux-musl"
else
    DISTRIBUTION="unknown-linux-gnu"
fi

# https://github.com/DataDog/libdatadog/releases/download/v0.7.0-rc.1/libdatadog-aarch64-alpine-linux-musl.tar.gz
TAR_LIBDATADOG=libdatadog-${MARCH}-${DISTRIBUTION}.tar.gz
GITHUB_URL_LIBDATADOG=https://github.com/DataDog/libdatadog/releases/download/${TAG_LIBDATADOG}/${TAR_LIBDATADOG}

SHA256_LIBDATADOG="blank"
while IFS='  ' read -r checksum filename; do
    if [ "$filename" == "$TAR_LIBDATADOG" ]; then
        SHA256_LIBDATADOG="$checksum  $filename"
        break
    fi
done < "$CHECKSUM_FILE"

if [ "$SHA256_LIBDATADOG" == "blank" ]; then
    echo "Could not find checksum for ${TAR_LIBDATADOG} in ${CHECKSUM_FILE}"
    exit 1
else
  echo "Using libdatadog sha256: ${SHA256_LIBDATADOG}"
fi

mkdir -p "$TARGET_EXTRACT" || true
cd "$TARGET_EXTRACT"

if [[ -e "${TAR_LIBDATADOG}" ]]; then
    already_present=1
else
    already_present=0
    echo "Downloading libdatadog ${GITHUB_URL_LIBDATADOG}..."
    if command -v curl > /dev/null 2>&1; then
        curl -fsSLO "${GITHUB_URL_LIBDATADOG}"
    elif command -v wget > /dev/null 2>&1; then
        wget -q -O "${GITHUB_URL_LIBDATADOG##*/}" "${GITHUB_URL_LIBDATADOG}"
    else
        echo "Error: neither curl nor wget is available." >&2
        exit 1
    fi
fi

echo "Checking libdatadog sha256"
if ! echo "${SHA256_LIBDATADOG}" | sha256sum -c -; then
    echo "Error validating libdatadog SHA256"
    echo "Please clear $TARGET_EXTRACT before restarting"
    exit 1
fi

if [[ $already_present -eq 0 || ! -f "cmake/DatadogConfig.cmake" ]]; then
    echo "Extracting ${TAR_LIBDATADOG}"
    tar xf "${TAR_LIBDATADOG}" --strip-components=1 --no-same-owner
fi
