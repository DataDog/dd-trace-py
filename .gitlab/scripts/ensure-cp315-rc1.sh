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

# Repoint the shim at the stale prefix directly so the pip bootstrap survives
# ${PY_DIR} being unlinked. finalize-one.sh regenerates this shim at the end.
if [[ -f "${SHIM}" ]]; then
  printf '#!/bin/sh\nexec "%s/bin/python" "$@"\n' "${stale_prefix}" >"${SHIM}"
  chmod +x "${SHIM}"
fi
# #region agent log
_dbg H1 "98" "shim repointed to stale prefix; py_dir freed" "{\"shim_body\":\"$(_json_str "$(cat "${SHIM}" 2>/dev/null)")\",\"py_dir_exists\":$([[ -e ${PY_DIR} ]] && echo true || echo false),\"bootstrap_version\":\"$(_json_str "$("${stale_prefix}/bin/python" -V 2>&1)")\"}"
_note "H1 shim_body_after=$(cat "${SHIM}" 2>/dev/null | tr '\n' ' ')"
_note "H1 py_dir_freed=$([[ -e ${PY_DIR} ]] && echo no || echo yes) bootstrap_version=$("${stale_prefix}/bin/python" -V 2>&1)"
# #endregion

install_cosign_if_needed

build_dir=$(mktemp -d)
trap 'rm -rf "${build_dir}"' EXIT
cd "${build_dir}"

export MANYLINUX_DISABLE_CLANG="${MANYLINUX_DISABLE_CLANG:-0}"
export MANYLINUX_DISABLE_CLANG_FOR_CPYTHON="${MANYLINUX_DISABLE_CLANG_FOR_CPYTHON:-0}"

"${BUILD_SCRIPTS}/build-cpython.sh" \
  hugo@python.org https://github.com/login/oauth "${TARGET_VERSION}"
# #region agent log
_dbg H3 "113" "build-cpython.sh finished" "{\"prefix_exists\":$([[ -x /opt/_internal/cpython-${TARGET_VERSION}/bin/python ]] && echo true || echo false)}"
# #endregion

"${BUILD_SCRIPTS}/finalize-one.sh" "/opt/_internal/cpython-${TARGET_VERSION}"
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
