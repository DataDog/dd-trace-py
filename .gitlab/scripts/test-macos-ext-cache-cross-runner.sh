#!/usr/bin/env bash
set -euo pipefail

mode="${1:?expected seed or restore}"
case "${mode}" in
  seed|restore) ;;
  *) echo "Unknown experiment mode: ${mode}" >&2; exit 2 ;;
esac

cd "${CI_PROJECT_DIR:?}"
if [[ -e .ext_cache || -e build ]]; then
  echo "Experiment requires a clean build directory and extension cache" >&2
  exit 1
fi

seed="${CI_PROJECT_DIR}/debugwheelhouse/cross-runner-seed"
output="${CI_PROJECT_DIR}/debugwheelhouse/cross-runner-${mode}"
archive="${seed}/ext-cache.tar.gz"
mkdir -p "${output}"
hostname > "${output}/hostname"

if [[ "${mode}" == "restore" ]]; then
  if [[ ! -s "${archive}" || ! -s "${seed}/hostname" || ! -s "${seed}/toolchain" ]]; then
    echo "Seed cache or metadata is missing" >&2
    exit 1
  fi
  if cmp -s "${seed}/hostname" "${output}/hostname"; then
    echo "Seed and restore landed on the same host. Retry the restore job to test a different host." >&2
    exit 1
  fi
  printf 'Seed host: %s\nRestore host: %s\n' "$(cat "${seed}/hostname")" "$(cat "${output}/hostname")"
fi

export DD_USE_SCCACHE=0 _DD_DEBUG_EXT=1
unset RUSTC_WRAPPER RUSTC_WORKSPACE_WRAPPER
export _DD_DEBUG_EXT_FILE="${output}/timings.log"
# shellcheck source=build-wheel-helpers.sh
source .gitlab/scripts/build-wheel-helpers.sh
setup
# Put the ordinary per-interpreter wheel trace in the experiment artifacts.
export DEBUG_WHEEL_DIR="${output}"

{
  git rev-parse HEAD
  uname -m
  sw_vers -buildVersion
  rustc -vV
  cargo --version
  xcrun --sdk macosx --show-sdk-version
  clang --version
  printf 'Deployment target: %s\n' "${MACOSX_DEPLOYMENT_TARGET}"
  uv run --no-project python -c 'import sys, sysconfig; print(sys.version); print(sysconfig.get_config_var("SOABI"))'
} > "${output}/toolchain"

if [[ "${mode}" == "restore" ]]; then
  # Compare the source revision, SDK, compiler, Rust toolchain and Python ABI before using the archive.
  diff -u "${seed}/toolchain" "${output}/toolchain"
  SECONDS=0
  tar -xzf "${archive}"
  echo "Cache unpack: ${SECONDS}s"
fi

SECONDS=0
restore_ext_cache 2>&1 | tee "${output}/restore.log"
if [[ "${mode}" == "restore" ]]; then
  if ! grep -Fq "Successfully copied " "${output}/restore.log" ||
    grep -Eq 'No cached files found|Failed to copy|ext_cache restore failed' "${output}/restore.log"; then
    echo "Expected a full compiled extension cache hit" >&2
    exit 1
  fi
fi
build_wheel
echo "${mode} restore plus build: ${SECONDS}s"

if [[ "${mode}" == "seed" ]]; then
  export EXT_CACHE_POLICY=pull-push
  save_ext_cache
  test -d .ext_cache
  tar -czf "${archive}" .ext_cache
  echo "Compressed extension cache bytes: $(wc -c < "${archive}")"
fi

repair_wheel
finalize
test_wheel
