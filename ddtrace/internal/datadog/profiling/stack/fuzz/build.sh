#!/bin/bash
set -e

TARGET=fuzz_echion_remote_read
BUILD_DIR=/tmp/fuzz/build
MANIFEST_FILE="${BUILD_DIR}/fuzz_binaries.txt"

# Get the directory where this script lives, then go up one level to the stack source
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "Building fuzz target: $TARGET"
echo "Source directory: $SOURCE_DIR"

cmake -S "${SOURCE_DIR}" -B "${BUILD_DIR}" \
      -DBUILD_FUZZING=ON -DBUILD_TESTING=OFF -DSTACK_USE_LIBFUZZER=ON \
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
    echo "✅ Registered binary in manifest: ${BINARY_PATH}"
else
    echo "❌ Binary not found or not executable: ${BINARY_PATH}"
    exit 1
fi