#!/usr/bin/env bash
# Run clang-tidy on profiling C++ source files
# This script uses run-clang-tidy for parallelization instead of CMake integration
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../.." && pwd)"
# All cmake binary trees go under the top-level build/ directory (gitignored by /build/).
BUILD_DIR="${REPO_ROOT}/build/profiling-clang-tidy"

echo "Script dir: ${SCRIPT_DIR}"
echo "Repo root: ${REPO_ROOT}"

# Build the rust native library first (required for cmake to find it).
# Set CARGO_TARGET_DIR so that FindLibNative.cmake can locate the generated C
# headers (datadog/profiling.h etc.) via $ENV{CARGO_TARGET_DIR}/include.
export CARGO_TARGET_DIR="${REPO_ROOT}/src/native/target"
echo "Building rust native library (CARGO_TARGET_DIR=${CARGO_TARGET_DIR})..."
cargo build --release \
    --manifest-path "${REPO_ROOT}/src/native/Cargo.toml" \
    --features profiling,crashtracker,stats,ffe

# Dedup Rust-generated C headers so downstream C++ code can include them without
# multiple-definition errors (mirrors the RunDedupHeaders.cmake POST_BUILD step
# used by the scikit-build-core build path).
_dedup_exe=$(command -v dedup_headers 2>/dev/null || true)
if [ -z "${_dedup_exe}" ] && [ -x "${HOME}/.cargo/bin/dedup_headers" ]; then
    _dedup_exe="${HOME}/.cargo/bin/dedup_headers"
fi
if [ -z "${_dedup_exe}" ]; then
    echo "dedup_headers not found — installing from libdatadog..."
    cargo install --git https://github.com/DataDog/libdatadog --bin dedup_headers tools
    _dedup_exe="${HOME}/.cargo/bin/dedup_headers"
fi
_host_triple=$(rustc -vV 2>/dev/null | grep '^host:' | awk '{print $2}')
cmake \
    "-DHEADERS_DIR=${CARGO_TARGET_DIR}/include/datadog" \
    "-DDEDUP_HEADERS_EXE=${_dedup_exe}" \
    "-DNATIVE_LIB=${CARGO_TARGET_DIR}/${_host_triple}/release/lib_native.so" \
    -P "${REPO_ROOT}/cmake/RunDedupHeaders.cmake"

# Configure cmake for dd_wrapper to generate compile_commands.json
echo "Configuring cmake for dd_wrapper..."
mkdir -p "${BUILD_DIR}/dd_wrapper"
pushd "${BUILD_DIR}/dd_wrapper"
cmake \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DCMAKE_BUILD_TYPE=Debug \
    -DPython3_ROOT_DIR=$(python3 -c "import sys; print(sys.prefix)") \
    -DEXTENSION_SUFFIX=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))") \
    -DNATIVE_EXTENSION_LOCATION="${REPO_ROOT}/ddtrace/internal/native" \
    "${SCRIPT_DIR}/dd_wrapper"
popd

# Configure cmake for stack
echo "Configuring cmake for stack..."
mkdir -p "${BUILD_DIR}/stack"
pushd "${BUILD_DIR}/stack"
cmake \
    -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
    -DCMAKE_BUILD_TYPE=Debug \
    -DPython3_ROOT_DIR=$(python3 -c "import sys; print(sys.prefix)") \
    -DEXTENSION_SUFFIX=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))") \
    -DNATIVE_EXTENSION_LOCATION="${REPO_ROOT}/ddtrace/internal/native" \
    -DDD_WRAPPER_DIR="${BUILD_DIR}/dd_wrapper" \
    -DDatadog_INCLUDE_DIRS="${BUILD_DIR}/dd_wrapper/include" \
    "${SCRIPT_DIR}/stack"
popd

# Merge compile_commands.json from all build directories
MERGED_COMPILE_COMMANDS="${BUILD_DIR}/compile_commands.json"
echo "Merging compile_commands.json files..."
COMPILE_COMMANDS_FILES=()
for subdir in dd_wrapper stack ddup; do
    if [[ -f "${BUILD_DIR}/${subdir}/compile_commands.json" ]]; then
        COMPILE_COMMANDS_FILES+=("${BUILD_DIR}/${subdir}/compile_commands.json")
    fi
done

if [[ ${#COMPILE_COMMANDS_FILES[@]} -eq 0 ]]; then
    echo "Error: No compile_commands.json files found after cmake configure"
    exit 1
fi

# Merge JSON arrays using jq
if [[ ${#COMPILE_COMMANDS_FILES[@]} -eq 1 ]]; then
    cp "${COMPILE_COMMANDS_FILES[0]}" "${MERGED_COMPILE_COMMANDS}"
else
    jq -s 'add' "${COMPILE_COMMANDS_FILES[@]}" > "${MERGED_COMPILE_COMMANDS}"
fi

echo "Using merged compile_commands.json from: ${BUILD_DIR}"

# Collect all profiling source files (not headers, not test files, not build artifacts)
# Also exclude fuzz sources since BUILD_FUZZING is OFF by default
SOURCE_FILES=()
while IFS= read -r -d '' file; do
    SOURCE_FILES+=("$file")
done < <(find "${SCRIPT_DIR}" \
    \( -name "*.cpp" -o -name "*.cc" \) \
    ! -path "*/build/*" \
    ! -path "*/CMakeFiles/*" \
    ! -path "*/test/*" \
    ! -path "*/fuzz/*" \
    ! -path "*/_vendor/*" \
    -print0)

echo "Found ${#SOURCE_FILES[@]} source files to analyze"

# Clang-tidy checks - focused set for speed
# Exclude checks that are too slow or noisy
CHECKS="bugprone-*,clang-analyzer-*,performance-*,-bugprone-easily-swappable-parameters,-clang-analyzer-security.insecureAPI.*,-performance-avoid-endl"

# Header filter to only analyze our headers, not system headers
HEADER_FILTER="${SCRIPT_DIR}/.*"

# Find run-clang-tidy (may be named differently on different systems)
RUN_CLANG_TIDY=""
for cmd in run-clang-tidy run-clang-tidy-20 run-clang-tidy-19 run-clang-tidy-18; do
    if command -v "$cmd" &>/dev/null; then
        RUN_CLANG_TIDY="$cmd"
        break
    fi
done

# Use parallel jobs
JOBS="${CMAKE_BUILD_PARALLEL_LEVEL:-$(nproc)}"

if [[ -n "${RUN_CLANG_TIDY}" ]]; then
    echo "Using ${RUN_CLANG_TIDY} with ${JOBS} parallel jobs"
    ${RUN_CLANG_TIDY} \
        -p "${BUILD_DIR}" \
        -j "${JOBS}" \
        -checks="${CHECKS}" \
        -header-filter="${HEADER_FILTER}" \
        -warnings-as-errors='*' \
        "${SOURCE_FILES[@]}"
else
    echo "run-clang-tidy not found, falling back to sequential clang-tidy"
    CLANG_TIDY="${CLANG_TIDY:-clang-tidy}"
    FAILED=0
    for file in "${SOURCE_FILES[@]}"; do
        echo "Analyzing: ${file}"
        if ! ${CLANG_TIDY} \
            -p "${BUILD_DIR}" \
            -checks="${CHECKS}" \
            -header-filter="${HEADER_FILTER}" \
            --warnings-as-errors='*' \
            "${file}"; then
            FAILED=1
        fi
    done
    if [[ ${FAILED} -ne 0 ]]; then
        exit 1
    fi
fi

echo "clang-tidy analysis complete"

