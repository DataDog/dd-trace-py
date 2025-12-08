#!/usr/bin/env bash
set -euo pipefail

# Source helper functions
source "$(dirname "$0")/build-wheel-helpers.sh"

setup

uv build --sdist --out-dir pywheels/
