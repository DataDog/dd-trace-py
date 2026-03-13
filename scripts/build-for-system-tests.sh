#!/usr/bin/env bash
# build-for-system-tests.sh
#
# Convenience script for building dd-trace-py artifacts suitable for use with
# system-tests parametric testing on ARM Mac (Apple Silicon).
#
# Two build modes are supported:
#
#   --wheel (default)
#     Builds a manylinux-compatible .whl file inside a linux/arm64 Docker
#     container. The resulting wheel is placed in dist/ and can be copied
#     into the system-tests binaries/ directory.
#
#   --inplace
#     Builds ARM64 .so extensions in-place using a linux/arm64 Docker
#     container. Use this mode together with a `python-load-from-local`
#     volume mount in system-tests. Pure-Python edits do not require a
#     rebuild, making iteration faster.
#
# Usage:
#   ./scripts/build-for-system-tests.sh [--wheel|--inplace]
#                                        [--python-version <version>]
#                                        [--copy-to <path>]
#
# Examples:
#   # Build wheel with defaults (Python 3.11)
#   ./scripts/build-for-system-tests.sh
#
#   # Build wheel for Python 3.12, then copy to system-tests binaries
#   ./scripts/build-for-system-tests.sh --wheel --python-version 3.12 \
#     --copy-to ../system-tests/binaries/
#
#   # In-place build for fast iteration
#   ./scripts/build-for-system-tests.sh --inplace

set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
MODE="wheel"
PYTHON_VERSION="3.11"
COPY_TO=""

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --wheel)
            MODE="wheel"
            shift
            ;;
        --inplace)
            MODE="inplace"
            shift
            ;;
        --python-version)
            PYTHON_VERSION="${2:?'--python-version requires a value (e.g. 3.11)'}"
            shift 2
            ;;
        --copy-to)
            COPY_TO="${2:?'--copy-to requires a destination path'}"
            shift 2
            ;;
        -h|--help)
            sed -n '/^# Usage:/,/^[^#]/{ /^#/{ s/^# \{0,2\}//; p }; /^[^#]/q }' "$0"
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Run '$0 --help' for usage." >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
info()  { echo "[build-for-system-tests] $*"; }
warn()  { echo "[build-for-system-tests] WARNING: $*" >&2; }
error() { echo "[build-for-system-tests] ERROR: $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

# Verify we are running from the repository root
if [[ ! -f "setup.py" && ! -f "pyproject.toml" ]]; then
    error "This script must be run from the root of the dd-trace-py repository."
fi

# Detect host architecture and warn if not ARM64
HOST_ARCH="$(uname -m)"
if [[ "$HOST_ARCH" != "arm64" && "$HOST_ARCH" != "aarch64" ]]; then
    warn "Host architecture is '$HOST_ARCH', not ARM64."
    warn "This script is primarily intended for Apple Silicon (ARM64) Macs."
    warn "Cross-compilation will be used; builds may be slow."
fi

# Check that Docker is available
if ! command -v docker &>/dev/null; then
    error "Docker is not installed or not in PATH. Please install Docker Desktop."
fi

if ! docker info &>/dev/null; then
    error "Docker daemon is not running. Please start Docker Desktop."
fi

# Ensure src/native/ is present (setuptools-rust needs it to build extensions)
if [[ ! -d "src/native" ]]; then
    warn "src/native/ directory not found. Attempting to restore via git..."
    if git checkout -- src/native/ 2>/dev/null; then
        info "src/native/ restored successfully."
    else
        error "Could not restore src/native/. Please ensure the directory exists."
    fi
fi

# ---------------------------------------------------------------------------
# Derive the Docker image tag from the requested Python version
# ---------------------------------------------------------------------------
DOCKER_IMAGE="python:${PYTHON_VERSION}-slim"

# Derive a CPython ABI tag for the output wheel name, e.g. cp311 for 3.11
PY_SHORT="cp$(echo "$PYTHON_VERSION" | tr -d '.')"

info "Build mode      : $MODE"
info "Python version  : $PYTHON_VERSION ($DOCKER_IMAGE)"
info "Host arch       : $HOST_ARCH"

# ---------------------------------------------------------------------------
# Mode: --wheel
# ---------------------------------------------------------------------------
if [[ "$MODE" == "wheel" ]]; then
    info "Building .whl inside linux/arm64 container..."

    mkdir -p dist

    docker run --rm \
        --platform linux/arm64 \
        -v "$(pwd)":/host-src:ro \
        -v "$(pwd)/dist":/host-dist \
        "$DOCKER_IMAGE" bash -c "
            set -euxo pipefail

            # Install system build dependencies
            apt-get update -qq
            apt-get install -y --no-install-recommends \
                gcc g++ cargo pkg-config cmake patchelf git curl ca-certificates

            # Copy the source tree (the host mount is read-only)
            cp -r /host-src /src
            cd /src

            # Remove any stale build artefacts from the host
            rm -rf .download_cache build dist

            # Install Python build tools
            pip install --quiet \
                wheel \
                'cmake>=3.24.2,<3.28' \
                cython \
                'setuptools-rust<2' \
                patchelf

            # Build the wheel
            pip wheel --no-build-isolation --no-deps -w /src/dist .

            # Copy the wheel back to the host
            cp /src/dist/*.whl /host-dist/
        "

    # Identify what was built
    WHEEL_FILES=(dist/${PY_SHORT}-${PY_SHORT}-linux_aarch64.whl)
    # Fall back to a glob if the exact name pattern differs
    if ! ls dist/*"${PY_SHORT}"*aarch64*.whl &>/dev/null; then
        WHEEL_FILES=(dist/*.whl)
    fi

    info "Build complete."
    info "Wheel(s) written to:"
    for f in dist/*.whl; do
        info "  $f"
    done

    # Optionally copy to the system-tests binaries directory
    if [[ -n "$COPY_TO" ]]; then
        if [[ ! -d "$COPY_TO" ]]; then
            error "Copy destination does not exist: $COPY_TO"
        fi
        for f in dist/*.whl; do
            cp "$f" "$COPY_TO/"
            info "Copied $(basename "$f") -> $COPY_TO/"
        done
    fi

# ---------------------------------------------------------------------------
# Mode: --inplace
# ---------------------------------------------------------------------------
elif [[ "$MODE" == "inplace" ]]; then
    info "Building ARM64 extensions in-place inside linux/arm64 container..."
    info "NOTE: Pure-Python changes do not require a rebuild in this mode."

    # Clear stale CMake/download caches that may contain x86_64 artefacts
    info "Clearing stale build caches..."
    rm -rf .download_cache/_cmake_deps build

    docker run --rm \
        --platform linux/arm64 \
        -v "$(pwd)":/dd-trace-py \
        "$DOCKER_IMAGE" bash -c "
            set -euxo pipefail

            # Install system build dependencies
            apt-get update -qq
            apt-get install -y --no-install-recommends \
                gcc g++ cargo pkg-config cmake patchelf git curl ca-certificates

            cd /dd-trace-py

            # Install Python build tools
            pip install --quiet \
                wheel \
                'cmake>=3.24.2,<3.28' \
                cython \
                'setuptools-rust<2' \
                patchelf

            # Build extensions in-place (editable install)
            pip install --no-build-isolation -e .
        "

    info "In-place build complete."
    info "ARM64 .so extensions are now present in the repository tree."
    info "Mount this directory as 'python-load-from-local' in system-tests."

    if [[ -n "$COPY_TO" ]]; then
        warn "--copy-to is not meaningful in --inplace mode and has been ignored."
    fi
fi

info "Done."
