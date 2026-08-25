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

# #region agent log
_dbg() {
  [ -n "${DD_DEBUG_LOG:-}" ] || return 0
  printf '{"sessionId":"1e7b4c","runId":"%s","hypothesisId":"%s","location":"ensure-cp315-rc1.sh:%s","message":"%s","data":%s,"timestamp":%s}\n' \
    "${DD_DEBUG_RUN_ID:-run1}" "$1" "$2" "$3" "$4" "$(($(date +%s) * 1000))" >>"${DD_DEBUG_LOG}" 2>/dev/null || true
}
_json_str() { printf '%s' "${1:-}" | tr -d '\n' | sed 's/\\/\\\\/g; s/"/\\"/g'; }
# Mirror key state to stdout so CI job logs are self-diagnosing when no
# DD_DEBUG_LOG is mounted.
_note() { echo "[ensure-cp315-rc1][dbg] $*"; }
# #endregion

if [[ ! -e "${PY_DIR}" ]]; then
  echo "[ensure-cp315-rc1] ${PY_DIR} not found" >&2
  # #region agent log
  _dbg H3 "24" "cp315 dir absent before rebuild" '{"py_dir_exists":false}'
  # #endregion
  exit 1
fi

if "${PY_DIR}/bin/python" -c 'import sys; sys.exit(0 if sys.version_info.releaselevel in ("candidate", "final") else 1)'; then
  echo "[ensure-cp315-rc1] cp315 already rc/final: $("${PY_DIR}/bin/python" -V 2>&1)"
  # #region agent log
  _dbg H4 "33" "no-op, already rc/final" "{\"version\":\"$(_json_str "$("${PY_DIR}/bin/python" -V 2>&1)")\"}"
  # #endregion
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

echo "[ensure-cp315-rc1] cp315 is pre-release ($("${PY_DIR}/bin/python" -V 2>&1)); rebuilding ${TARGET_VERSION}..."

# finalize-one.sh bootstraps pip for the freshly built interpreter by running
# "${SHIM} -m pip --python <new prefix>", and ${SHIM} is a two-line wrapper that
# execs ${PY_DIR}/bin/python. It then ends with a plain `ln -s <prefix> ${PY_DIR}`
# (no -f). So the stale interpreter must stay reachable for the pip bootstrap
# while the ${PY_DIR} name itself must be free for the final symlink.
stale_prefix=$(readlink -f "${PY_DIR}")
# #region agent log
_dbg H1 "70" "resolved stale prefix and shim state" "{\"stale_prefix\":\"$(_json_str "${stale_prefix}")\",\"py_dir_is_symlink\":$([[ -L ${PY_DIR} ]] && echo true || echo false),\"shim_exists\":$([[ -f ${SHIM} ]] && echo true || echo false),\"shim_body\":\"$(_json_str "$(cat "${SHIM}" 2>/dev/null)")\"}"
_note "H1 stale_prefix=${stale_prefix} py_dir_is_symlink=$([[ -L ${PY_DIR} ]] && echo yes || echo no) shim_exists=$([[ -f ${SHIM} ]] && echo yes || echo no)"
_note "H1 shim_body_before=$(cat "${SHIM}" 2>/dev/null | tr '\n' ' ')"
# #endregion

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
  # #region agent log
  _dbg H1 "85" "bootstrap python missing after freeing py_dir" "{\"stale_prefix\":\"$(_json_str "${stale_prefix}")\"}"
  # #endregion
  exit 1
fi

# The shim is rewritten after the interpreter is built: finalize-one.sh only
# needs it at its pip step, and leaving it dangling until then is harmless.
# #region agent log
_dbg H1 "98" "py_dir freed; shim selection deferred until after build" "{\"py_dir_exists\":$([[ -e ${PY_DIR} ]] && echo true || echo false),\"stale_version\":\"$(_json_str "$("${stale_prefix}/bin/python" -V 2>&1)")\"}"
_note "H1 py_dir_freed=$([[ -e ${PY_DIR} ]] && echo no || echo yes) stale_version=$("${stale_prefix}/bin/python" -V 2>&1)"
# #endregion

# #region agent log
# H6 (rejected): the stale interpreter lacks ssl -- it reports OpenSSL 3.5.6 fine.
# H9 (rejected): build-cpython.sh's openssl cleanup breaks a working ssl -- the
#   shared libraries are present both before and after it runs.
# H11: the rebuilt interpreter has no _ssl because the image ships no openssl
#   headers, so `pip --python <new prefix>` fails inside the *target* interpreter.
_probe_py() {
  local py=$1 label=$2 ssl_v pip_v
  if [[ ! -x ${py} ]]; then
    _note "probe ${label} python=${py} state=MISSING"
    _dbg H6 "probe" "bootstrap candidate probe" "{\"label\":\"$(_json_str "${label}")\",\"python\":\"$(_json_str "${py}")\",\"present\":false}"
    return 1
  fi
  local ssl_ok pip_ok
  ssl_v=$("${py}" -c 'import ssl; print(ssl.OPENSSL_VERSION)' 2>&1 | tail -1) && ssl_ok=true || ssl_ok=false
  pip_v=$("${py}" -c 'import pip; print(pip.__version__)' 2>&1 | tail -1) && pip_ok=true || pip_ok=false
  _note "probe ${label} python=${py} ssl_ok=${ssl_ok} ssl=${ssl_v} pip_ok=${pip_ok} pip=${pip_v}"
  _dbg H6 "probe" "bootstrap candidate probe" "{\"label\":\"$(_json_str "${label}")\",\"python\":\"$(_json_str "${py}")\",\"present\":true,\"ssl_ok\":${ssl_ok},\"ssl\":\"$(_json_str "${ssl_v}")\",\"pip_ok\":${pip_ok},\"pip\":\"$(_json_str "${pip_v}")\"}"
  [[ ${ssl_ok} == true ]] && [[ ${pip_ok} == true ]]
}

_probe_openssl_tree() {
  local label=$1 libs top ssl_h
  libs=$(ls "${OPENSSL_PREFIX}/lib" 2>&1 | tr '\n' ',' | head -c 300 || true)
  top=$(ls "${OPENSSL_PREFIX}" 2>&1 | tr '\n' ',' | head -c 200 || true)
  ssl_h=$([[ -f "${OPENSSL_PREFIX}/include/openssl/ssl.h" ]] && echo yes || echo no)
  _note "probe openssl(${label}) top=${top} ssl_h=${ssl_h} lib=${libs}"
  _dbg H9 "probe" "openssl tree state" "{\"label\":\"$(_json_str "${label}")\",\"top\":\"$(_json_str "${top}")\",\"ssl_h\":\"${ssl_h}\",\"libs\":\"$(_json_str "${libs}")\"}"
}

_probe_openssl_tree before-build
_probe_py "${stale_prefix}/bin/python" "stale-b1(before-build)" || true
# #endregion

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
# #region agent log
# H11: were the openssl headers the reason the rebuilt interpreter had no _ssl?
_dbg H11 "openssl-headers" "openssl header state after restore" "{\"ssl_h\":$([[ -f ${OPENSSL_PREFIX}/include/openssl/ssl.h ]] && echo true || echo false)}"
_note "H11 openssl_ssl_h_present=$([[ -f ${OPENSSL_PREFIX}/include/openssl/ssl.h ]] && echo yes || echo no)"
# #endregion

export MANYLINUX_DISABLE_CLANG="${MANYLINUX_DISABLE_CLANG:-0}"
export MANYLINUX_DISABLE_CLANG_FOR_CPYTHON="${MANYLINUX_DISABLE_CLANG_FOR_CPYTHON:-0}"

"${BUILD_SCRIPTS}/build-cpython.sh" \
  hugo@python.org https://github.com/login/oauth "${TARGET_VERSION}"
new_prefix="/opt/_internal/cpython-${TARGET_VERSION}"
# build-cpython.sh installs bin/python3; finalize-one.sh is what adds the
# bin/python symlink, so probe the interpreter under the name that exists now.
new_python="${new_prefix}/bin/python3"
# #region agent log
_dbg H3 "113" "build-cpython.sh finished" "{\"prefix_exists\":$([[ -x ${new_python} ]] && echo true || echo false)}"
_probe_openssl_tree after-build
_probe_py "${stale_prefix}/bin/python" "stale-b1(after-build)" || true
_probe_py "${new_python}" "new-rc1(after-build)" || true
# #endregion

# finalize-one.sh bootstraps pip with "${SHIM} -m pip --python <new prefix>".
# pip's --python re-executes pip under the *target* interpreter, so the new
# interpreter is the one that needs a working ssl module; the shim only has to
# supply pip's own code. Point it at the stale interpreter, which keeps its pip.
printf '#!/bin/sh\nexec "%s/bin/python" "$@"\n' "${stale_prefix}" >"${SHIM}"
chmod +x "${SHIM}"

# A from-source build in this image drops any extension whose headers are absent,
# and it does so silently: the failure only shows up much later as an opaque pip
# error or a ModuleNotFoundError in the smoke test. Report every missing module
# at once, here, instead of rediscovering them one job at a time.
"${new_python}" - <<'PY'
import importlib
import sys

# Needed by the wheel build, the pip bootstrap, or ddtrace itself at import time.
REQUIRED = [
    "ctypes", "_ctypes", "ssl", "zlib", "bz2", "lzma", "sqlite3", "hashlib",
    "_socket", "select", "unicodedata", "_multiprocessing", "mmap", "_json",
    "_datetime", "_queue", "fcntl", "binascii", "_struct", "array",
]
# Absent from the base image too; nothing in the build path depends on them.
OPTIONAL = ["_curses", "readline", "_uuid", "_tkinter", "_decimal", "compression.zstd"]


def missing(names):
    out = []
    for name in names:
        try:
            importlib.import_module(name)
        except Exception as exc:
            out.append(f"{name}({type(exc).__name__})")
    return out


missing_optional = missing(OPTIONAL)
print("[ensure-cp315-rc1][dbg] H12 missing_optional=" + (",".join(missing_optional) or "none"))

missing_required = missing(REQUIRED)
if missing_required:
    print(
        "[ensure-cp315-rc1] interpreter is missing required stdlib modules: "
        + ", ".join(missing_required)
        + " -- a build dependency is absent from the image",
        file=sys.stderr,
    )
    sys.exit(1)
print("[ensure-cp315-rc1][dbg] H12 required_stdlib=complete")
PY

# finalize-one.sh closes with `ln -s cpython3.15 /usr/local/bin/python3.15`
# without -f, under `set -e`, so that name must be free as well as ${PY_DIR}.
# #region agent log
_dbg H10 "versioned-link" "versioned python link state before finalize" "{\"path\":\"/usr/local/bin/python3.15\",\"exists\":$([[ -e /usr/local/bin/python3.15 || -L /usr/local/bin/python3.15 ]] && echo true || echo false)}"
_note "H10 python3.15_link_exists=$([[ -e /usr/local/bin/python3.15 || -L /usr/local/bin/python3.15 ]] && echo yes || echo no)"
# #endregion
rm -f /usr/local/bin/python3.15

"${BUILD_SCRIPTS}/finalize-one.sh" "${new_prefix}"
# #region agent log
_dbg H3 "118" "finalize-one.sh finished" "{\"py_dir_target\":\"$(_json_str "$(readlink -f "${PY_DIR}" 2>/dev/null)")\",\"shim_body\":\"$(_json_str "$(cat "${SHIM}" 2>/dev/null)")\"}"
_note "H3 finalize_ok py_dir_target=$(readlink -f "${PY_DIR}" 2>/dev/null) shim_body=$(cat "${SHIM}" 2>/dev/null | tr '\n' ' ')"
# #endregion

# Guard against a silent downgrade: the wheel must not be built against 3.15.0b*.
if ! "${PY_DIR}/bin/python" -c 'import sys; sys.exit(0 if sys.version_info.releaselevel in ("candidate", "final") else 1)'; then
  echo "[ensure-cp315-rc1] rebuild did not yield rc/final: $("${PY_DIR}/bin/python" -V 2>&1)" >&2
  # #region agent log
  _dbg H4 "126" "rebuild left a pre-release interpreter" "{\"version\":\"$(_json_str "$("${PY_DIR}/bin/python" -V 2>&1)")\"}"
  # #endregion
  exit 1
fi

if [[ "${stale_prefix}" != "/opt/_internal/cpython-${TARGET_VERSION}" ]]; then
  rm -rf "${stale_prefix}"
fi

echo "[ensure-cp315-rc1] rebuilt cp315: $("${PY_DIR}/bin/python" -V 2>&1)"
# #region agent log
_dbg H4 "137" "rebuild verified" "{\"version\":\"$(_json_str "$("${PY_DIR}/bin/python" -V 2>&1)")\",\"pip\":\"$(_json_str "$("${PY_DIR}/bin/python" -m pip --version 2>&1 | head -c 120)")\"}"
_note "H4 final_version=$("${PY_DIR}/bin/python" -V 2>&1) pip=$("${PY_DIR}/bin/python" -m pip --version 2>&1 | head -1)"
# #endregion
