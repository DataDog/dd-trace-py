#!/usr/bin/env bash
set -euo pipefail

# Source helper functions
source "$(dirname "$0")/build-wheel-helpers.sh"

setup
restore_ext_cache
build_wheel
save_ext_cache
repair_wheel
finalize
test_wheel

# Show sccache stats if available
if command -v sccache &> /dev/null; then
  sccache --show-stats || true
fi
