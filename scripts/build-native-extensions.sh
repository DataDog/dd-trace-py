#!/bin/bash

# Build dd-trace-py native extensions (.so files) for Linux using the testrunner image
# This script detects the exact Python version from the python:X.Y-slim Docker image
# and builds the native extensions to match that version.

set -eu

# Dynamically determine the repository root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
DD_TRACE_PY_ROOT="$( cd "${SCRIPT_DIR}/.." && pwd )"

usage() {
    cat <<EOF
Usage: $0 [OPTIONS] [PYTHON_VERSION]

Build dd-trace-py native extensions for Linux containers.

Arguments:
  PYTHON_VERSION    Python major.minor version (e.g., 3.11, 3.12, 3.13)
                    Defaults to 3.11 if not specified

Options:
  --platform ARCH   Docker platform (linux/amd64 or linux/arm64/v8)
                    Auto-detects from host architecture if not specified
  -h, --help        Show this help message

Examples:
  $0                              # Build for Python 3.11 (auto-detect platform)
  $0 3.12                         # Build for Python 3.12 (auto-detect platform)
  $0 --platform linux/amd64 3.11  # Build for Python 3.11 on AMD64/x86_64
  $0 --platform linux/arm64/v8    # Build for Python 3.11 (default) on ARM64

Notes:
  - On ARM64 Macs, builds for linux/arm64/v8 (native, fast)
  - On x86_64 machines, builds for linux/amd64
  - Cross-platform builds use emulation (slow, ~10-30 minutes)
  - Built .so files are cached in your dd-trace-py directory for reuse

EOF
    exit 0
}

# Default values
PYTHON_VERSION="3.11"
DOCKER_PLATFORM=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            usage
            ;;
        --platform)
            DOCKER_PLATFORM="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            usage
            ;;
        *)
            PYTHON_VERSION="$1"
            shift
            ;;
    esac
done

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
fi

# Detect architecture if not specified
if [[ -z "$DOCKER_PLATFORM" ]]; then
    ARCH=$(uname -m)
    case $ARCH in
        arm64|aarch64)
            DOCKER_PLATFORM="linux/arm64/v8"
            ;;
        *)
            DOCKER_PLATFORM="linux/amd64"
            ;;
    esac
    echo "Auto-detected platform: ${DOCKER_PLATFORM}"
else
    echo "Using specified platform: ${DOCKER_PLATFORM}"
fi

echo "==================================================================="
echo "Building dd-trace-py native extensions for Python ${PYTHON_VERSION}"
echo "Target platform: ${DOCKER_PLATFORM}"
echo "==================================================================="
echo

# Detect exact Python version from the python:X.Y-slim image
echo "Step 1: Detecting exact Python version from python:${PYTHON_VERSION}-slim..."
PYTHON_VERSION=$(docker run --rm --platform "${DOCKER_PLATFORM}" "python:${PYTHON_VERSION}-slim" python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')")
echo "Detected Python version: $PYTHON_VERSION"
echo

# Clean up old build files for this Python version
echo "Step 2: Cleaning up old build files for Python ${PYTHON_VERSION}..."
OLD_FILES=$(find "${DD_TRACE_PY_ROOT}/ddtrace" -name "*cpython-${PYTHON_VERSION//.}*linux-gnu.so" -type f 2>/dev/null | wc -l | tr -d ' ')
if [[ "$OLD_FILES" -gt 0 ]]; then
    echo "Removing $OLD_FILES old .so files..."
    find "${DD_TRACE_PY_ROOT}/ddtrace" -name "*cpython-${PYTHON_VERSION//.}*linux-gnu.so" -type f -delete
    echo "Cleanup complete."
else
    echo "No old files found."
fi
echo

# Build the native extensions using the python:X.Y-slim image with full build environment
echo "Step 3: Building native extensions using python:${PYTHON_VERSION}-slim image..."
if [[ "$DOCKER_PLATFORM" == "linux/arm64/v8" ]]; then
    echo "Building natively on ARM64 (fast)..."
else
    echo "This may take 10-30 minutes on ARM64 Macs (emulation)..."
fi
echo

docker run --rm --platform "${DOCKER_PLATFORM}" --network host \
  -v "${DD_TRACE_PY_ROOT}:/dd-trace-py" \
  "python:${PYTHON_VERSION}-slim" \
  bash -c "set -eu && \
    echo 'Python version:' && \
    python --version && \
    echo 'Python extension suffix:' && \
    python -c 'import sysconfig; print(sysconfig.get_config_var(\"EXT_SUFFIX\"))' && \
    echo && \
    echo 'Installing build dependencies (including Rust)...' && \
    apt-get update > /dev/null 2>&1 && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y gcc g++ make cmake git curl > /dev/null 2>&1 && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y > /dev/null 2>&1 && \
    . \"\$HOME/.cargo/env\" && \
    echo 'Build dependencies installed.' && \
    echo && \
    echo 'Building dd-trace-py...' && \
    python -m pip install -e /dd-trace-py"

echo
echo "==================================================================="
echo "SUCCESS! Native extensions built for Python ${PYTHON_VERSION}"
echo "==================================================================="
echo

