#!/usr/bin/env bash
set -euo pipefail

# Source helper functions
source "$(dirname "$0")/build-wheel-helpers.sh"

setup
build_wheel
repair_wheel
finalize
test_wheel

# Show sccache stats if available
if command -v sccache &> /dev/null; then
  sccache --show-stats || true
fi
