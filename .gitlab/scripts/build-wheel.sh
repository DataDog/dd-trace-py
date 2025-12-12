#!/usr/bin/env bash
set -euo pipefail

# Source helper functions
source "$(dirname "$0")/build-wheel-helpers.sh"

setup
build_wheel
repair_wheel
finalize
test_wheel
