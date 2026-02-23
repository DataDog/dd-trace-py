#!/bin/bash
set -e

TARGET=fuzz_echion_remote_read
BUILD_DIR=/tmp/fuzz/build
MANIFEST_FILE="${BUILD_DIR}/fuzz_binaries.txt"

# Get the directory where this script lives, then go up one level to the stack source
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROFILING_DIR="$(cd "${SOURCE_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PROFILING_DIR}/../../../.." && pwd)"

echo "Building fuzz target: $TARGET"
echo "Source directory: $SOURCE_DIR"
echo "Profiling directory: $PROFILING_DIR"

# ---- Step 1: Build Rust (_native + libdatadog headers) ----
echo "=== Building Rust dependencies ==="

# Ensure Rust toolchain is available
if ! command -v cargo &> /dev/null; then
    echo "Rust not found, installing via rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    . "$HOME/.cargo/env"
fi

pip3 install setuptools_rust cython cmake --break-system-packages 2>/dev/null || \
    pip3 install setuptools_rust cython cmake
(cd "${REPO_ROOT}" && python3 setup.py build_rust --inplace)

# Derive key paths (same variables as build_standalone.sh)
EXTENSION_SUFFIX="$(python3 -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")"
NATIVE_EXTENSION_LOCATION="$(cd "${PROFILING_DIR}/../../native" && pwd)"
LIB_INSTALL_DIR="${BUILD_DIR}/lib"

echo "EXTENSION_SUFFIX: ${EXTENSION_SUFFIX}"
echo "NATIVE_EXTENSION_LOCATION: ${NATIVE_EXTENSION_LOCATION}"
echo "LIB_INSTALL_DIR: ${LIB_INSTALL_DIR}"

mkdir -p "${LIB_INSTALL_DIR}"

# ---- Step 2: Build dd_wrapper ----
echo "=== Building dd_wrapper ==="
DD_WRAPPER_DIR="${PROFILING_DIR}/dd_wrapper"
DD_WRAPPER_BUILD="${BUILD_DIR}/dd_wrapper"

cmake -S "${DD_WRAPPER_DIR}" -B "${DD_WRAPPER_BUILD}" \
      -DNATIVE_EXTENSION_LOCATION="${NATIVE_EXTENSION_LOCATION}" \
      -DEXTENSION_SUFFIX="${EXTENSION_SUFFIX}" \
      -DLIB_INSTALL_DIR="${LIB_INSTALL_DIR}" \
      -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DCMAKE_C_FLAGS="-O1 -g -fno-omit-frame-pointer" \
      -DCMAKE_CXX_FLAGS="-O1 -g -fno-omit-frame-pointer"

cmake --build "${DD_WRAPPER_BUILD}" -j
cmake --build "${DD_WRAPPER_BUILD}" --target install

# ---- Step 3: Build fuzz target ----
echo "=== Building fuzz target ==="
cmake -S "${SOURCE_DIR}" -B "${BUILD_DIR}" \
      -DBUILD_FUZZING=ON -DBUILD_TESTING=OFF -DSTACK_USE_LIBFUZZER=ON \
      -DLIB_INSTALL_DIR="${LIB_INSTALL_DIR}" \
      -DEXTENSION_SUFFIX="${EXTENSION_SUFFIX}" \
      -DNATIVE_EXTENSION_LOCATION="${NATIVE_EXTENSION_LOCATION}" \
      -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DCMAKE_C_FLAGS="-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined" \
      -DCMAKE_CXX_FLAGS="-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined" \
      -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=address,undefined" \
  && cmake --build "${BUILD_DIR}" -j --target $TARGET

# Register the built binary in the manifest file for the CI infrastructure to discover
BINARY_PATH="${BUILD_DIR}/fuzz/${TARGET}"
if [ -x "${BINARY_PATH}" ]; then
    echo "${BINARY_PATH}" >> "${MANIFEST_FILE}"
    echo "Registered binary in manifest: ${BINARY_PATH}"
else
    echo "Binary not found or not executable: ${BINARY_PATH}"
    exit 1
fi
