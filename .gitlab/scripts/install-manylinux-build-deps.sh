#!/usr/bin/env bash
# Install build deps that the upstream pypa manylinux2014 images do not ship.
# The derived dd-trace-py images bake these in; the pypa mirror used for the
# cp315 rebuild does not. EPEL-7 aarch64 carries a smaller package set than
# x86_64, so each package is best-effort: zstd-sys vendors its own zstd source.
#
# No openssl here on purpose. install-build-packages.sh installs openssl-devel
# and build-openssl.sh then removes it again, and nothing links the system
# openssl anyway -- the native crate's TLS is rustls/ring, with no openssl-sys
# in the lockfile, and CPython links the image's own /opt/_internal/openssl-*.
set -uo pipefail

PACKAGES=(autoconf automake libtool libzstd-devel libzstd-static)

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
