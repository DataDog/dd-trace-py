#!/usr/bin/env bash
# Rebuild cp315 when the mirrored manylinux2014 image still ships 3.15.0b*.
# 3.15.0b1 SIGSEGVs during the native ddtrace wheel build; 3.15.0rc1+ does not.
# Drop this script once registry.ddbuild.io mirrors pypa manylinux2014 >= 2026.08.04-1.
set -euo pipefail

TARGET_VERSION=3.15.0rc1
ABI_TAG=cp315-cp315
PY_DIR="/opt/python/${ABI_TAG}"
SHIM=/usr/local/bin/cpython3.15
BUILD_SCRIPTS=/opt/_internal/build_scripts

# The published image is a runtime image: it keeps shared libraries but not the
# headers they were built against (no ffi.h, no bzlib.h, and only
# ${OPENSSL_PREFIX}/lib since build-cpython.sh strips bin/, include/ and
# lib/pkgconfig after each interpreter). Prebuilt interpreters keep working, but
# a from-source build silently drops every extension whose headers are absent --
# _ctypes and _ssl among them. So restore the build-time dependencies first.
OPENSSL_PREFIX=/opt/_internal/openssl-3.5
OPENSSL_VERSION=3.5.6
OPENSSL_SHA256=deae7c80cba99c4b4f940ecadb3c3338b13cb77418409238e57d7f31f2a3b736

if [[ ! -e "${PY_DIR}" ]]; then
  echo "[ensure-cp315-rc1] ${PY_DIR} not found" >&2
  exit 1
fi

if "${PY_DIR}/bin/python" -c 'import sys; sys.exit(0 if sys.version_info.releaselevel in ("candidate", "final") else 1)'; then
  echo "[ensure-cp315-rc1] cp315 already rc/final: $("${PY_DIR}/bin/python" -V 2>&1)"
  exit 0
fi

install_cosign_if_needed() {
  if command -v cosign >/dev/null 2>&1; then
    return 0
  fi

  # build-cpython.sh verifies the CPython tarball with sigstore. pypa copies cosign
  # into the build stage only, so it is absent from the published runtime image.
  local cosign_version=3.1.3
  local arch="${AUDITWHEEL_ARCH:-$(uname -m)}"
  local cosign_arch
  case "${arch}" in
    x86_64 | amd64) cosign_arch=amd64 ;;
    aarch64 | arm64) cosign_arch=arm64 ;;
    *)
      echo "[ensure-cp315-rc1] unsupported arch for cosign: ${arch}" >&2
      exit 1
      ;;
  esac

  echo "[ensure-cp315-rc1] installing cosign v${cosign_version} for ${cosign_arch}..."
  curl -fsSL --retry 10 \
    "https://github.com/sigstore/cosign/releases/download/v${cosign_version}/cosign-linux-${cosign_arch}" \
    -o /usr/local/bin/cosign
  chmod +x /usr/local/bin/cosign
}

ensure_openssl_headers() {
  if [[ -f "${OPENSSL_PREFIX}/include/openssl/ssl.h" ]]; then
    return 0
  fi

  # The rebuilt openssl overwrites the shared libraries every other interpreter
  # in the image links against, so refuse to proceed on a version mismatch.
  local installed
  installed=$("${stale_prefix}/bin/python" -c 'import ssl; print(ssl.OPENSSL_VERSION.split()[1])')
  if [[ ${installed} != "${OPENSSL_VERSION}" ]]; then
    echo "[ensure-cp315-rc1] image ships openssl ${installed} but this script pins" \
      "${OPENSSL_VERSION}; update OPENSSL_VERSION/OPENSSL_SHA256" >&2
    exit 1
  fi

  echo "[ensure-cp315-rc1] restoring openssl ${OPENSSL_VERSION} headers for the _ssl build..."
  OPENSSL_ROOT="openssl-${OPENSSL_VERSION}" \
    OPENSSL_HASH="${OPENSSL_SHA256}" \
    OPENSSL_DOWNLOAD_URL="https://github.com/openssl/openssl/releases/download/openssl-${OPENSSL_VERSION}" \
    "${BUILD_SCRIPTS}/build-openssl.sh"
}

echo "[ensure-cp315-rc1] cp315 is pre-release ($("${PY_DIR}/bin/python" -V 2>&1)); rebuilding ${TARGET_VERSION}..."

# finalize-one.sh bootstraps pip for the freshly built interpreter by running
# "${SHIM} -m pip --python <new prefix>", and ${SHIM} is a two-line wrapper that
# execs ${PY_DIR}/bin/python. It then ends with a plain `ln -s <prefix> ${PY_DIR}`
# (no -f). So the stale interpreter must stay reachable for the pip bootstrap
# while the ${PY_DIR} name itself must be free for the final symlink.
stale_prefix=$(readlink -f "${PY_DIR}")

if [[ -L "${PY_DIR}" ]]; then
  rm -f "${PY_DIR}"
else
  # Not a symlink: move the real directory aside so the name is free.
  stale_prefix="/opt/_internal/.stale-${ABI_TAG}"
  rm -rf "${stale_prefix}"
  mv "${PY_DIR}" "${stale_prefix}"
fi

if [[ ! -x "${stale_prefix}/bin/python" ]]; then
  echo "[ensure-cp315-rc1] no usable bootstrap python at ${stale_prefix}" >&2
  exit 1
fi

# build-openssl.sh and build-cpython.sh both fetch and unpack into the current
# directory, so move out of the CI checkout before calling either.
build_dir=$(mktemp -d)
trap 'rm -rf "${build_dir}"' EXIT
cd "${build_dir}"

install_cosign_if_needed
# manylinux's own compile-dependency list (libffi-devel, bzip2-devel, xz-devel,
# uuid-devel, ...). It also installs the distro openssl-devel, which
# build-openssl.sh then replaces -- that is upstream's own ordering.
"${BUILD_SCRIPTS}/install-build-packages.sh"
ensure_openssl_headers

export MANYLINUX_DISABLE_CLANG="${MANYLINUX_DISABLE_CLANG:-0}"
export MANYLINUX_DISABLE_CLANG_FOR_CPYTHON="${MANYLINUX_DISABLE_CLANG_FOR_CPYTHON:-0}"

"${BUILD_SCRIPTS}/build-cpython.sh" \
  hugo@python.org https://github.com/login/oauth "${TARGET_VERSION}"
new_prefix="/opt/_internal/cpython-${TARGET_VERSION}"
# build-cpython.sh installs bin/python3; finalize-one.sh is what adds the
# bin/python symlink, so use the name that exists now.
new_python="${new_prefix}/bin/python3"

# finalize-one.sh bootstraps pip with "${SHIM} -m pip --python <new prefix>".
# pip's --python re-executes pip under the *target* interpreter, so the new
# interpreter is the one that needs a working ssl module; the shim only has to
# supply pip's own code. Point it at the stale interpreter, which keeps its pip.
printf '#!/bin/sh\nexec "%s/bin/python" "$@"\n' "${stale_prefix}" >"${SHIM}"
chmod +x "${SHIM}"

# A from-source build in this image drops any extension whose headers are absent,
# and it does so silently: the failure only surfaces much later as an opaque pip
# error or a ModuleNotFoundError in the smoke test. Report every missing module
# at once, here, instead of rediscovering them one job at a time.
"${new_python}" - <<'PY'
import importlib
import sys

# Needed by the wheel build, the pip bootstrap, or ddtrace itself at import time.
# _curses, readline, _uuid, _tkinter, _decimal and compression.zstd are absent
# from the base image too, and nothing in the build path depends on them.
REQUIRED = [
    "ctypes", "_ctypes", "ssl", "zlib", "bz2", "lzma", "sqlite3", "hashlib",
    "_socket", "select", "unicodedata", "_multiprocessing", "mmap", "_json",
    "_datetime", "_queue", "fcntl", "binascii", "_struct", "array",
]

missing = []
for name in REQUIRED:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{name}({type(exc).__name__})")

if missing:
    print(
        "[ensure-cp315-rc1] interpreter is missing required stdlib modules: "
        + ", ".join(missing)
        + " -- a build dependency is absent from the image",
        file=sys.stderr,
    )
    sys.exit(1)
PY

# finalize-one.sh closes with `ln -s cpython3.15 /usr/local/bin/python3.15`
# without -f, under `set -e`, so that name must be free as well as ${PY_DIR}.
rm -f /usr/local/bin/python3.15

"${BUILD_SCRIPTS}/finalize-one.sh" "${new_prefix}"

# Guard against a silent downgrade: the wheel must not be built against 3.15.0b*.
if ! "${PY_DIR}/bin/python" -c 'import sys; sys.exit(0 if sys.version_info.releaselevel in ("candidate", "final") else 1)'; then
  echo "[ensure-cp315-rc1] rebuild did not yield rc/final: $("${PY_DIR}/bin/python" -V 2>&1)" >&2
  exit 1
fi

if [[ "${stale_prefix}" != "/opt/_internal/cpython-${TARGET_VERSION}" ]]; then
  rm -rf "${stale_prefix}"
fi

echo "[ensure-cp315-rc1] rebuilt cp315: $("${PY_DIR}/bin/python" -V 2>&1)"
