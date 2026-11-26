#!/usr/bin/env bash
set -euo pipefail

# Source helper functions
source "$(dirname "$0")/build-wheel-helpers.sh"
source "$(dirname "$0")/macos-wheel-cache.sh"

setup
macos_cache_mode=$(macos_wheel_cache_mode)
case "${macos_cache_mode}" in
  read) restore_macos_wheel_cache ;;
  publish) ;;
  *) restore_ext_cache ;;
esac
build_wheel
save_ext_cache
if [[ -n "${MACOS_CACHE_SYMBOLS:-}" ]]; then
  repair_wheel --reuse-dsym-from "${MACOS_CACHE_SYMBOLS}"
else
  repair_wheel
fi
if [[ "${macos_cache_mode}" == publish ]]; then
  stage_macos_wheel_cache
fi
finalize
test_wheel
if [[ "${macos_cache_mode}" == publish ]]; then
  publish_macos_wheel_cache
fi

# Show sccache stats if available
if command -v sccache &> /dev/null; then
  sccache --show-stats || true
fi
