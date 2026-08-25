#!/usr/bin/env bash
# Rebuild cp315 when the mirrored manylinux2014 image still ships 3.15.0b*.
# 3.15.0b1 SIGSEGVs during the native ddtrace wheel build; 3.15.0rc1+ does not.
# Drop this script once registry.ddbuild.io mirrors pypa manylinux2014 >= 2026.08.04-1.
set -euo pipefail

PY315=/opt/python/cp315-cp315/bin/python
if [[ ! -x "${PY315}" ]]; then
  echo "[ensure-cp315-rc1] ${PY315} not found" >&2
  exit 1
fi

if "${PY315}" -c 'import sys; sys.exit(0 if sys.version_info.releaselevel in ("candidate", "final") else 1)'; then
  echo "[ensure-cp315-rc1] cp315 already rc/final: $(${PY315} -V)"
  exit 0
fi

install_cosign_if_needed() {
  if command -v cosign >/dev/null 2>&1; then
    return 0
  fi

  # Match pypa/manylinux Dockerfile MANYLINUX_COSIGN_VERSION (build stage only).
  local cosign_version=3.1.3
  local arch="${AUDITWHEEL_ARCH:-$(uname -m)}"
  local cosign_arch
  case "${arch}" in
    x86_64|amd64) cosign_arch=amd64 ;;
    aarch64|arm64) cosign_arch=arm64 ;;
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

echo "[ensure-cp315-rc1] cp315 is pre-release ($(${PY315} -V)); rebuilding 3.15.0rc1..."
install_cosign_if_needed

rm -rf /opt/python/cp315-cp315 /opt/_internal/cpython-3.15.0b*
build_dir=$(mktemp -d)
trap 'rm -rf "${build_dir}"' EXIT
cd "${build_dir}"

export MANYLINUX_DISABLE_CLANG="${MANYLINUX_DISABLE_CLANG:-0}"
export MANYLINUX_DISABLE_CLANG_FOR_CPYTHON="${MANYLINUX_DISABLE_CLANG_FOR_CPYTHON:-0}"

/opt/_internal/build_scripts/build-cpython.sh \
  hugo@python.org https://github.com/login/oauth 3.15.0rc1
/opt/_internal/build_scripts/finalize-one.sh /opt/_internal/cpython-3.15.0rc1
/opt/python/cp315-cp315/bin/python -V
