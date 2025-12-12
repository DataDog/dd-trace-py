#!/usr/bin/env bash
set -euo pipefail

# Source helper functions
source "$(dirname "$0")/build-wheel-helpers.sh"

# Skipped sccache setup for sdist build
setup

section_start "build_sdist" "Building source distribution"
uv build --sdist --out-dir pywheels/
section_end "build_sdist"
