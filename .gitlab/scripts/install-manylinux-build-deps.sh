#!/usr/bin/env bash
# Install build deps that the upstream pypa manylinux2014 images do not ship.
# The derived dd-trace-py images bake these in; the pypa mirror used for the
# cp315 rebuild does not. EPEL-7 aarch64 carries a smaller package set than
# x86_64, so each package is best-effort: zstd-sys vendors its own zstd source,
# and CPython links the image's own /opt/_internal/openssl-*.
set -uo pipefail

PACKAGES=(autoconf automake libtool openssl-devel openssl-static libzstd-devel libzstd-static)

# #region agent log
_dbg() {
  [ -n "${DD_DEBUG_LOG:-}" ] || return 0
  printf '{"sessionId":"1e7b4c","runId":"%s","hypothesisId":"%s","location":"install-manylinux-build-deps.sh:%s","message":"%s","data":%s,"timestamp":%s}\n' \
    "${DD_DEBUG_RUN_ID:-run1}" "$1" "$2" "$3" "$4" "$(($(date +%s) * 1000))" >>"${DD_DEBUG_LOG}" 2>/dev/null || true
}
# #endregion

installed=()
missing=()
for pkg in "${PACKAGES[@]}"; do
  if yum install -y "${pkg}"; then
    installed+=("${pkg}")
  else
    echo "[install-manylinux-build-deps] optional dep unavailable, continuing: ${pkg}"
    missing+=("${pkg}")
  fi
done

_join() { local IFS=,; printf '%s' "$*"; }

echo "[install-manylinux-build-deps] installed: $(_join "${installed[@]+"${installed[@]}"}")"
echo "[install-manylinux-build-deps] missing:   $(_join "${missing[@]+"${missing[@]}"}")"

# #region agent log
_dbg H2 "37" "dependency install summary" "{\"arch\":\"${AUDITWHEEL_ARCH:-$(uname -m)}\",\"installed\":\"$(_join "${installed[@]+"${installed[@]}"}")\",\"missing\":\"$(_join "${missing[@]+"${missing[@]}"}")\"}"
_dbg H5 "38" "system zstd/openssl probe" "{\"zstd_h\":$([ -e /usr/include/zstd.h ] && echo true || echo false),\"libzstd_a\":$(ls /usr/lib64/libzstd.a >/dev/null 2>&1 && echo true || echo false),\"internal_openssl\":\"$(find /opt/_internal -maxdepth 1 -name 'openssl*' 2>/dev/null | head -1)\"}"
# #endregion
