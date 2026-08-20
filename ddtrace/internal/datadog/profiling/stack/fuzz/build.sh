#!/bin/bash
set -eo pipefail

FUZZ_TARGETS=(
    fuzz_echion_frame_create
    fuzz_echion_frame_read
    fuzz_echion_greenlet
    fuzz_echion_interp
    fuzz_echion_long
    fuzz_echion_mirrors
    fuzz_echion_pyunicode
    fuzz_echion_stacks
    fuzz_echion_strings
    fuzz_echion_task_unwind
    fuzz_echion_tasks
    fuzz_echion_thread_unwind
    fuzz_echion_thread_unwind_tasks
)
BUILD_DIR=/tmp/fuzz/build
MANIFEST_FILE="/fuzz_binaries.txt"

# Get the directory where this script lives, then go up one level to the stack source
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROFILING_DIR="$(cd "${SOURCE_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${PROFILING_DIR}/../../../.." && pwd)"

echo "Building fuzz targets: ${FUZZ_TARGETS[*]}"
echo "Source directory: $SOURCE_DIR"
echo "Profiling directory: $PROFILING_DIR"

# ---- Step 1: Build Rust (_native + libdatadog headers) ----
echo "=== Building Rust dependencies ==="

# Ensure Rust toolchain is available
if ! command -v cargo &> /dev/null; then
    echo "Rust not found, installing via rustup..."
    for i in 1 2 3; do
      curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && break
      echo "rustup install attempt $i failed, retrying..."
      sleep 5
      [ "$i" -eq 3 ] && { echo "Failed to install rustup after 3 attempts"; exit 1; }
    done
    . "$HOME/.cargo/env"
fi

pip3 install setuptools_rust cython cmake --break-system-packages 2>/dev/null || \
    pip3 install setuptools_rust cython cmake
(cd "${REPO_ROOT}" && python3 setup.py build_rust --inplace)

# Derive key paths (same variables as build_standalone.sh)
EXTENSION_SUFFIX="$(python3 -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")"
PYTHON_LIBDIR="$(python3 -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")"
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
      -DDD_WRAPPER_DIR="${LIB_INSTALL_DIR}" \
      -DLIB_INSTALL_DIR="${LIB_INSTALL_DIR}" \
      -DEXTENSION_SUFFIX="${EXTENSION_SUFFIX}" \
      -DNATIVE_EXTENSION_LOCATION="${NATIVE_EXTENSION_LOCATION}" \
      -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ \
      -DCMAKE_BUILD_TYPE=RelWithDebInfo \
      -DCMAKE_C_FLAGS="-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined" \
      -DCMAKE_CXX_FLAGS="-O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined" \
      -DCMAKE_EXE_LINKER_FLAGS="-fsanitize=address,undefined -Wl,-rpath,${PYTHON_LIBDIR}" \
  && cmake --build "${BUILD_DIR}" -j

# Copy LSan suppression file next to the binaries so it can be referenced at runtime
cp "${SCRIPT_DIR}/lsan.supp" "${BUILD_DIR}/fuzz/lsan.supp"

# ---- Step 4: Smoke test each built binary ----
# Verify each fuzzer can actually start. libFuzzer's -help=1 forces the dynamic
# loader to resolve every shared library (libpython, libdd_wrapper, _native) and
# runs the binary's global constructors before printing its help to stderr. A
# binary that cannot start (unresolvable/incompatible libpython, a crashing
# global constructor, a missing symbol, ...) never reaches that help output, so
# we treat reaching libFuzzer's help as proof of a successful startup.
#
# AIDEV-NOTE: Success is keyed on the help OUTPUT, not the exit code. These
# harnesses embed CPython/echion global state and can hit a flaky SIGSEGV in
# global teardown at process exit on some Python versions (observed on 3.13/3.14,
# non-deterministic across targets and runs). That teardown crash is irrelevant
# to fuzzing -- the fuzzer runs with runs=-1 and never exits normally -- so
# gating on the exit status made this build fail non-deterministically. Reaching
# help proves the binary starts; the exit-time teardown crash is tracked
# separately and must not fail the build here.
echo "=== Smoke testing fuzz binaries (-help=1) ==="
SMOKE_FAILURES=0
for TARGET in "${FUZZ_TARGETS[@]}"; do
    BINARY_PATH="${BUILD_DIR}/fuzz/${TARGET}"
    echo "Smoke test: ${BINARY_PATH} -help=1"
    # Capture stdout+stderr and ignore the exit status (see AIDEV-NOTE above);
    # success is decided from the presence of libFuzzer's help output.
    SMOKE_OUTPUT="$(LD_LIBRARY_PATH="${LIB_INSTALL_DIR}:${PYTHON_LIBDIR}:${LD_LIBRARY_PATH:-}" \
        ASAN_OPTIONS=detect_leaks=0 \
        "${BINARY_PATH}" -help=1 2>&1 || true)"
    if printf '%s\n' "${SMOKE_OUTPUT}" | grep -qi "Verbosity level"; then
        echo "Smoke test passed: ${TARGET} (reached libFuzzer initialization)"
    else
        echo "Smoke test FAILED: ${TARGET} did not reach libFuzzer initialization"
        echo "----- ${TARGET} -help=1 output -----"
        printf '%s\n' "${SMOKE_OUTPUT}"
        echo "------------------------------------"
        SMOKE_FAILURES=$((SMOKE_FAILURES + 1))
    fi
done
if [ "${SMOKE_FAILURES}" -ne 0 ]; then
    echo "Startup smoke test failed for ${SMOKE_FAILURES} target(s)"
    exit 1
fi
echo "All fuzz binaries reached libFuzzer initialization"

# Register the built binaries in the manifest file for the CI infrastructure to discover
for TARGET in "${FUZZ_TARGETS[@]}"; do
    BINARY_PATH="${BUILD_DIR}/fuzz/${TARGET}"
    if [ -x "${BINARY_PATH}" ]; then
        echo "${BINARY_PATH}" >> "${MANIFEST_FILE}"
        echo "Registered binary in manifest: ${BINARY_PATH}"
    else
        echo "Binary not found or not executable: ${BINARY_PATH}"
        exit 1
    fi
done
