#!/usr/bin/env bash
set -euo pipefail

# Source helper functions
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/build-wheel-helpers.sh"

if [[ "$(uname -s)" == "Darwin" ]]; then
  source "${SCRIPT_DIR}/setup-libclang-macos.sh"
fi

setup
uv run --no-project python "${SCRIPT_DIR}/check-libclang.py"
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
