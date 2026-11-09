#!/usr/bin/env bash
set -euo pipefail

# Source helper functions
source "$(dirname "$0")/build-wheel-helpers.sh"

SCCACHE_VERSION="0.15.0"
# sccache keys include absolute source paths, and setup.py puts CARGO_TARGET_DIR (which is also
# hashed) inside the source tree. pip unpacks an sdist into a random directory, so build from a
# fixed one instead or every run would miss the cache.
SDIST_DIR="/tmp/ddtrace-sdist"
SDIST_FILES=("${CI_PROJECT_DIR}"/pywheels/*.tar.gz)
SDIST_FILE="${SDIST_FILES[0]}"

# alpine_build doesn't ship sccache, so download the version the musllinux images build.
install_sccache() {
  local sha256 name tmp status=0
  case "$(uname -m)" in
    aarch64) sha256="3a6a3712b49da3d263bf2d30d702de4302793016019e800bfb81c0c69401d8f8" ;;
    x86_64) sha256="782d2b5dd7ae0a55ebe368ab258114d0928d019ac2d949ab85d5d02f3926709e" ;;
    *) return 1 ;;
  esac
  name="sccache-v${SCCACHE_VERSION}-$(uname -m)-unknown-linux-musl"
  tmp=$(mktemp -d)
  curl --fail -sSL --connect-timeout 10 --max-time 120 --retry 5 --retry-delay 2 --retry-connrefused \
      -o "${tmp}/sccache.tar.gz" \
      "https://github.com/mozilla/sccache/releases/download/v${SCCACHE_VERSION}/${name}.tar.gz" \
    && echo "${sha256}  ${tmp}/sccache.tar.gz" | sha256sum -c \
    && tar -xzf "${tmp}/sccache.tar.gz" -C "${tmp}" \
    && "${tmp}/${name}/sccache" --version \
    && install -m 0755 "${tmp}/${name}/sccache" /usr/local/bin/sccache \
    || status=1
  rm -rf "${tmp}"
  return "${status}"
}

# Best effort: without sccache, setup.py warns and builds without a compiler cache.
section_start "install_sccache" "Installing sccache"
if ! command -v sccache > /dev/null; then
  install_sccache || echo "WARNING: could not install sccache ${SCCACHE_VERSION}, building without a compiler cache"
fi
section_end "install_sccache"
if command -v sccache > /dev/null; then
  # Print stats on any exit, so failed builds report them too
  trap 'sccache --show-stats || true' EXIT
fi

rm -rf "${SDIST_DIR}"
mkdir -p "${SDIST_DIR}"
tar -xzf "${SDIST_FILE}" -C "${SDIST_DIR}" --strip-components=1

cd "$(mktemp -d)"
twine check "${SDIST_FILE}"

section_start "install_sdist" "Installing ${SDIST_FILE##*/}"
pip install "${SDIST_DIR}"
section_end "install_sdist"

python "${CI_PROJECT_DIR}/tests/smoke_test.py"
