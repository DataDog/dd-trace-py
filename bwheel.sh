#!/usr/bin/env bash

set -e -o pipefail

VERSION=${1:-311}
MODE=${2:-"unset"}

if [ "$MODE" == "unset" ]; then
    echo "Mode is unset, please set it to 'wheel', 'install', 'cc' or 'inplace'"
    exit 1
elif [ "$MODE" != "wheel" ] && [ "$MODE" != "install" ] && [ "$MODE" != "cc" ] && [ "$MODE" != "inplace" ]; then
    echo "Unknown mode: ${MODE}, please set it to 'wheel', 'install', 'cc' or 'inplace'"
    exit 1
fi

# Check that venv(version) exists
if [ ! -d "venv${VERSION}" ]; then
    echo "Version is invalid: venv${VERSION} does not exist"
    echo "Existing venvs:"
    ls -d venv*
    exit 1
fi

echo "Using version: ${VERSION}, mode: ${MODE}"

source venv${VERSION}/bin/activate

export DD_FAST_BUILD=1
export CMAKE_BUILD_PARALLEL_LEVEL=16

# python -m pip install --upgrade pip setuptools wheel cmake setuptools_rust cython wrapt bytecode envier lz4 grpcio protobuf

# python3 setup.py build_ext --inplace

if [ "$MODE" == "cc" ]; then
    time bear -- pip wheel . --no-deps --no-build-isolation -v
elif [ "$MODE" == "wheel" ]; then
    # time uv build --wheel
    time pip wheel . -v # --no-deps -v --no-build-isolation --no-clean
elif [ "$MODE" == "inplace" ]; then
    time python3 setup.py build_ext --inplace -v
elif [ "$MODE" == "install" ]; then
    time pip install -e . -v # --no-clean --no-deps --no-build-isolation
else
    echo "Unknown mode: ${MODE}"
    exit 1
fi

# ./install.sh "${VERSION}"
