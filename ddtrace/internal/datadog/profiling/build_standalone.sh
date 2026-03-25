#!/usr/bin/env bash
set -euo pipefail

### Useful globals
MY_DIR=$(dirname $(realpath $0))
MY_NAME="$0"
REPO_ROOT=$(realpath "${MY_DIR}/../../../..")
# All cmake binary trees go under the top-level build/ directory (gitignored by /build/).
BUILD_DIR="${REPO_ROOT}/build/profiling-standalone"
BUILD_MODE="Debug"

### Compiler discovery
# Initialize variables to store the highest versions
highest_cc=""
highest_cxx=""
highest_gcc=""
highest_gxx=""
highest_clang=""
highest_clangxx=""

if [[ $OSTYPE == 'darwin'* ]]; then
  # Needed for some of ForkDeathTests to pass on Mac
  export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
fi

# Make sure bc is installed
if ! command -v bc &> /dev/null; then
  echo "Error: bc not found. Please install bc."
  exit 1
fi

# Function to find the highest version of compilers
# Note that the product of this check is ignored if the user passes CC/CXX
find_highest_compiler_version() {
  local base_name=$1
  local highest_var_name=$2
  local highest_version=0
  local current_version
  local found_version=""

  # Function to extract the numeric version from the compiler output
  get_version() {
    $1 --version 2>&1 | grep -oE '[0-9]+(\.[0-9]+)?' | head -n 1
  }

  # Try to find the latest versions of both GCC and Clang (numbered versions)
  # The range 5-20 is arbitrary (GCC 5 was released in 2015, and 20 is a high number since Clang is on version 17)
  for version in {20..5}; do
    if command -v "${base_name}-${version}" &> /dev/null; then
      current_version=$(get_version "${base_name}-${version}")
      if (( $(echo "$current_version > $highest_version" | bc -l) )); then
        highest_version=$current_version
        found_version="${base_name}-${version}"
      fi
    fi
  done

  # Check the base version if it exists
  if command -v "$base_name" &> /dev/null; then
    current_version=$(get_version "$base_name")
    if (( $(echo "$current_version > $highest_version" | bc -l) )); then
      found_version="$base_name"
    fi
  fi

  # Assign the result to the variable name passed
  if [[ -n $found_version ]]; then
    eval "$highest_var_name=$found_version"
  fi
}

# Find highest versions for each compiler
find_highest_compiler_version cc highest_cc
find_highest_compiler_version c++ highest_cxx
find_highest_compiler_version gcc highest_gcc
find_highest_compiler_version g++ highest_gxx
find_highest_compiler_version clang highest_clang
find_highest_compiler_version clang++ highest_clangxx

# Get the highest clang_tidy from the $highest_clangxx variable
CLANGTIDY_CMD=${highest_clangxx/clang++/clang-tidy}

### Build setup
# Targets to target dirs
declare -A target_dirs
target_dirs["ddup"]="ddup"
target_dirs["stack"]="stack"
target_dirs["dd_wrapper"]="dd_wrapper"

# Compiler options
declare -A compiler_args
compiler_args["address"]="-DSANITIZE_OPTIONS=address"
compiler_args["leak"]="-DSANITIZE_OPTIONS=leak"
compiler_args["undefined"]="-DSANITIZE_OPTIONS=undefined"
compiler_args["safety"]="-DSANITIZE_OPTIONS=address,leak,undefined"
compiler_args["thread"]="-DSANITIZE_OPTIONS=thread"
compiler_args["numerical"]="-DSANITIZE_OPTIONS=integer,nullability,signed-integer-overflow,bounds,float-divide-by-zero"
compiler_args["dataflow"]="-DSANITIZE_OPTIONS=dataflow"
compiler_args["memory"]="-DSANITIZE_OPTIONS=memory"
compiler_args["fanalyzer"]="-DDO_FANALYZE=ON"
compiler_args["cppcheck"]="-DDO_CPPCHECK=ON"
compiler_args["infer"]="-DDO_INFER=ON"
compiler_args["clangtidy"]="-DDO_CLANGTIDY=ON"
compiler_args["clangtidy_cmd"]="-DCLANGTIDY_CMD=${CLANGTIDY_CMD}"
compiler_args["valgrind"]="-DDO_VALGRIND=ON -DCMAKE_CXX_FLAGS=-gdwarf-4 -DCMAKE_C_FLAGS=-gdwarf-4"

ctest_args=()

# Initial cmake args
cmake_args=(
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  -DCMAKE_VERBOSE_MAKEFILE=ON
  -DLIB_INSTALL_DIR=$(realpath $MY_DIR)/lib
  -DDD_WRAPPER_DIR=$(realpath $MY_DIR)/lib
  -DPython3_ROOT_DIR=$(python3 -c "import sys; print(sys.prefix)")
  -DNATIVE_EXTENSION_LOCATION=$(realpath $MY_DIR)/../../native
  -DEXTENSION_SUFFIX=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
)

# Initial build targets; start out empty
targets=()

set_cc() {
  if [ -z "${CC:-}" ]; then
    export CC=$highest_cc
  fi
  if [ -z "${CXX:-}" ]; then
    export CXX=$highest_cxx
  fi
  cmake_args+=(
    -DCMAKE_C_COMPILER=$CC
    -DCMAKE_CXX_COMPILER=$CXX
  )
}

# Helper functions for finding the compiler(s)
set_clang() {
  # Check that clang is available (required for most build modes)
  if [[ -z "$highest_clang" ]] || [[ -z "$highest_clangxx" ]]; then
    echo "Error: clang/clang++ not found. This build mode requires clang."
    exit 1
  fi

  if [ -z "${CC:-}" ]; then
    export CC=$highest_clang
  fi
  if [ -z "${CXX:-}" ]; then
    export CXX=$highest_clangxx
  fi
  cmake_args+=(
    -DCMAKE_C_COMPILER=$CC
    -DCMAKE_CXX_COMPILER=$CXX
  )
}

set_gcc() {
  # Check that gcc/g++ is available (required for most build modes)
  if [[ -z "$highest_gcc" ]] || [[ -z "$highest_gxx" ]]; then
    echo "Error: gcc/g++ not found. This build mode requires gcc."
    exit 1
  fi

  # Only set CC or CXX if they're not set
  if [ -z "${CC:-}" ]; then
    export CC=$highest_gcc
  fi
  if [ -z "${CXX:-}" ]; then
    export CXX=$highest_gxx
  fi
  cmake_args+=(
    -DCMAKE_C_COMPILER=$CC
    -DCMAKE_CXX_COMPILER=$CXX
  )
}

### Build runners
run_cmake() {
  target=$1
  dir=${target_dirs[$target]}
  build=${BUILD_DIR}/${dir}
  if [ -z "$dir" ]; then
    echo "No directory specified for cmake"
    exit 1
  fi

  # Make sure we have the build directory
  mkdir -p ${build} && pushd ${build} || { echo "Failed to create build directory for $dir"; exit 1; }

  # Remove stale cmake cache to avoid configuration conflicts
  rm -f CMakeCache.txt

  # Run cmake
  cmake "${cmake_args[@]}" -S=$MY_DIR/$dir || { echo "cmake failed"; exit 1; }
  cmake --build . || { echo "build failed"; exit 1; }
  if [[ " ${cmake_args[*]} " =~ " -DDO_CPPCHECK=ON " ]]; then
    echo "--------------------------------------------------------------------- Running CPPCHECK"
    make cppcheck || { echo "cppcheck failed"; exit 1; }
  fi
  if [[ " ${cmake_args[*]} " =~ " -DBUILD_TESTING=ON " ]]; then
    echo "--------------------------------------------------------------------- Running Tests"
    if command -v nproc &> /dev/null; then
      NPROC=$(nproc)
    else
      NPROC=$(getconf _NPROCESSORS_ONLN)
    fi
    ctest -j${NPROC} ${ctest_args[*]} --output-on-failure || { echo "tests failed!"; exit 1; }
  fi

  # OK, the build or whatever went fine I guess.
  popd
}

### Print help
print_help() {
  echo "Usage: ${MY_NAME} [options] [build_mode] [target]"
  echo "Options (one of)"
  echo "  -h, --help        Show this help message and exit"
  echo "  -a, --address     Clang + " ${compiler_args["address"]}
  echo "  -l, --leak        Clang + " ${compiler_args["leak"]}
  echo "  -u, --undefined   Clang + " ${compiler_args["undefined"]}
  echo "  -s, --safety      Clang + " ${compiler_args["safety"]}
  echo "  -t, --thread      Clang + " ${compiler_args["thread"]}
  echo "  -n, --numerical   Clang + " ${compiler_args["numerical"]}
  echo "  -d, --dataflow    Clang + " ${compiler_args["dataflow"]}  # Requires custom libstdc++ to work
  echo "  -m  --memory      Clang + " ${compiler_args["memory"]}
  echo "  -C  --cppcheck    Clang + " ${compiler_args["cppcheck"]}
  echo "  -I  --infer       Clang + " ${compiler_args["infer"]}
  echo "  -T  --clangtidy   Clang + " ${compiler_args["clangtidy"]}
  echo "  -f, --fanalyze    GCC   + " ${compiler_args["fanalyzer"]}
  echo "  -v, --valgrind    Clang + Valgrind + " ${compiler_args["valgrind"]}
  echo "  -c, --clang       Clang (alone)"
  echo "  -g, --gcc         GCC (alone)"
  echo "  --                Don't do anything special"
  echo ""
  echo "Build Modes:"
  echo "  Debug (default)"
  echo "  Release"
  echo "  RelWithDebInfo"
  echo ""
  echo "(any possible others, depending on what cmake supports for BUILD_TYPE out of the box)"
  echo ""
  echo "Targets:"
  echo "  all_test (default)"
  echo "  stack (also builds dd_wrapper)"
  echo "  stack_test (also builds dd_wrapper_test)"
  echo "  ddup (also builds dd_wrapper)"
  echo "  ddup_test (also builds dd_wrapper_test)"
}

print_cmake_args() {
  echo "CMake Args: ${cmake_args[*]}"
  echo "Targets: ${targets[*]}"
}

print_ctest_args() {
  echo "CTest Args: ${ctest_args[*]}"
}

### Check input
# Check the first slot, options
add_compiler_args() {
  case "$1" in
    -h|--help)
      print_help
      exit 0
      ;;
    -a|--address)
      cmake_args+=(${compiler_args["address"]})
      set_clang
      ;;
    -l|--leak)
      cmake_args+=(${compiler_args["leak"]})
      set_clang
      ;;
    -u|--undefined)
      cmake_args+=(${compiler_args["undefined"]})
      set_clang
      ;;
    -s|--safety)
      cmake_args+=(${compiler_args["safety"]})
      set_clang
      ;;
    -t|--thread)
      cmake_args+=(${compiler_args["thread"]})
      set_clang
      ;;
    -n|--numerical)
      cmake_args+=(${compiler_args["numerical"]})
      set_clang
      ;;
    -d|--dataflow)
      cmake_args+=(${compiler_args["dataflow"]})
      set_clang
      ;;
    -m|--memory)
      cmake_args+=(${compiler_args["memory"]})
      set_clang
      ;;
    -v|--valgrind)
      cmake_args+=(${compiler_args["valgrind"]})
      ctest_args+="-T memcheck"
      set_clang
      ;;
    -C|--cppcheck)
      cmake_args+=(${compiler_args["cppcheck"]})
      set_clang
      if command -v cppcheck &> /dev/null; then
        cmake_args+=(-DCPPCHECK_EXECUTABLE=$(which cppcheck))
      fi
      ;;
    -I|--infer)
      cmake_args+=(${compiler_args["infer"]})
      set_clang
      if command -v infer &> /dev/null; then
        cmake_args+=(-DInfer_EXECUTABLE=$(which infer))
      fi
      ;;
    -T|--clangtidy)
      cmake_args+=(${compiler_args["clangtidy"]})
      cmake_args+=(${compiler_args["clangtidy_cmd"]})
      set_clang
      ;;
    -f|--fanalyze)
      cmake_args+=(${compiler_args["fanalyzer"]})
      set_gcc
      ;;
    -c|--clang)
      set_clang
      ;;
    -g|--gcc)
      set_gcc
      ;;
    --)
      set_cc # Use system default compiler
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
}

# Check the second slot, build mode
add_build_mode() {
  case "$1" in
    Debug|Release|RelWithDebInfo)
      cmake_args+=(-DCMAKE_BUILD_TYPE=$1)
      BUILD_MODE=$1
      ;;
    ""|--)
      cmake_args+=(-DCMAKE_BUILD_TYPE=Debug)
      ;;
    *)
      echo "Unknown build mode: $1"
      exit 1
      ;;
  esac
}

# Check the third slot, target
add_target() {
  arg=${1:-"all_test"}
  if [[ "${arg}" =~ _test$ ]]; then
    cmake_args+=(-DBUILD_TESTING=ON)
  fi
  target=${arg%_test}

  case "${target}" in
    all|--)
      targets+=("stack")
      targets+=("ddup")
      ;;
    stack)
      targets+=("stack")
      ;;
    ddup)
      targets+=("ddup")
      ;;
    *)
      echo "Unknown target: $1"
      exit 1
      ;;
  esac
}

#Build rust dependencies
build_rust() {
    echo "Building Rust dependencies"
    local _cargo_profile="release"
    if [ "${BUILD_MODE}" = "Debug" ]; then _cargo_profile="debug"; fi
    local _profile_flag="--release"
    if [ "${BUILD_MODE}" = "Debug" ]; then _profile_flag=""; fi

    # Set CARGO_TARGET_DIR so FindLibNative.cmake can locate the generated C
    # headers (datadog/profiling.h etc.) via $ENV{CARGO_TARGET_DIR}/include.
    # Use the version-tagged path that matches the legacy setup.py convention so
    # that existing build trees are reused when both build systems co-exist.
    local _py_ver
    _py_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    export CARGO_TARGET_DIR="${MY_DIR}/../../../../src/native/target${_py_ver}"

    cargo build ${_profile_flag} \
        --manifest-path "${MY_DIR}/../../../../src/native/Cargo.toml" \
        --features profiling,crashtracker,stats,ffe

    # FindLibNative.cmake creates a cmake IMPORTED target whose IMPORTED_LOCATION
    # points to NATIVE_EXTENSION_LOCATION/_native<EXT_SUFFIX>.  CMake tracks this
    # file as a build dependency, so it must exist before cmake configures the
    # dd_wrapper build.  Copy the Cargo artifact there now, renaming it from the
    # Cargo output name (lib_native.so / lib_native.dylib) to the Python ABI name.
    local _ext_suffix
    _ext_suffix=$(python3 -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
    local _native_dest="${MY_DIR}/../../native/_native${_ext_suffix}"
    local _host_triple
    _host_triple=$(rustc -vV 2>/dev/null | grep '^host:' | awk '{print $2}')
    local _cargo_lib=""
    # When cargo builds natively (no --target flag) output is at <target_dir>/<profile>/;
    # when cross-compiling (--target <triple>) it is at <target_dir>/<triple>/<profile>/.
    # Check the native path first, then the triple-qualified path.
    for _prefix in "${CARGO_TARGET_DIR}" "${CARGO_TARGET_DIR}/${_host_triple}"; do
        for _ext in so dylib; do
            local _candidate="${_prefix}/${_cargo_profile}/lib_native.${_ext}"
            if [ -f "${_candidate}" ]; then
                _cargo_lib="${_candidate}"
                break 2
            fi
        done
    done
    if [ -z "${_cargo_lib}" ]; then
        echo "Error: could not find Rust artifact under ${CARGO_TARGET_DIR}/${_host_triple}/${_cargo_profile}/"
        exit 1
    fi
    mkdir -p "$(dirname "${_native_dest}")"
    cp "${_cargo_lib}" "${_native_dest}"
    echo "Installed Rust extension: ${_native_dest}"
}

# Run dedup_headers on the Rust-generated C headers so that dd_wrapper can include them without
# multiple-definition errors (the same post-processing that RunDedupHeaders.cmake does in the
# scikit-build-core build path).
run_dedup_headers() {
    local _headers_dir="${CARGO_TARGET_DIR}/include/datadog"
    if [ ! -d "${_headers_dir}" ]; then
        echo "dedup_headers: ${_headers_dir} does not exist, skipping"
        return 0
    fi

    # Find or install dedup_headers.
    local _dedup_exe
    _dedup_exe=$(command -v dedup_headers 2>/dev/null || echo "")
    if [ -z "${_dedup_exe}" ] && [ -x "${HOME}/.cargo/bin/dedup_headers" ]; then
        _dedup_exe="${HOME}/.cargo/bin/dedup_headers"
    fi
    if [ -z "${_dedup_exe}" ]; then
        echo "dedup_headers not found in PATH or ~/.cargo/bin — installing from libdatadog..."
        cargo install --git https://github.com/DataDog/libdatadog --bin dedup_headers tools \
            || { echo "Failed to install dedup_headers"; return 1; }
        _dedup_exe="${HOME}/.cargo/bin/dedup_headers"
    fi

    # Determine native lib path: <CARGO_TARGET_DIR>/<host_triple>/<profile>/lib_native.{so,dylib}
    local _cargo_profile="release"
    if [ "${BUILD_MODE}" = "Debug" ]; then _cargo_profile="debug"; fi
    local _host_triple
    _host_triple=$(rustc -vV 2>/dev/null | grep '^host:' | awk '{print $2}')
    local _native_lib=""
    for _ext in so dylib; do
        local _candidate="${CARGO_TARGET_DIR}/${_host_triple}/${_cargo_profile}/lib_native.${_ext}"
        if [ -f "${_candidate}" ]; then
            _native_lib="${_candidate}"
            break
        fi
    done

    echo "Running dedup_headers on ${_headers_dir}"
    cmake \
        "-DHEADERS_DIR=${_headers_dir}" \
        "-DDEDUP_HEADERS_EXE=${_dedup_exe}" \
        "-DNATIVE_LIB=${_native_lib}" \
        -P "${MY_DIR}/../../../../cmake/RunDedupHeaders.cmake" \
        || { echo "dedup_headers step failed"; return 1; }
}

### ENTRYPOINT
# Check for basic input validity
if [ $# -eq 0 ]; then
  echo "No arguments given.  At least one is needed, otherwise I'd (a m b i g u o u s l y) do a lot of work!"
  print_help
  exit 1
fi

if [ $# -ne 3 ]; then
  echo "Error: Expected exactly 3 arguments"
  print_help
  exit 1
fi

add_compiler_args "$1"
add_build_mode "$2"
add_target "$3"

# Print cmake args
print_cmake_args

print_ctest_args

build_rust
run_dedup_headers

run_cmake "dd_wrapper"

# Install dd_wrapper to the expected location so other targets can find it
pushd ${BUILD_DIR}/dd_wrapper || { echo "Failed to enter dd_wrapper build directory"; exit 1; }
cmake --build . --target install || { echo "dd_wrapper install failed"; exit 1; }
popd

# The Rust-generated C headers (datadog/profiling.h etc.) are copied by
# FindLibNative.cmake into the dd_wrapper cmake binary tree under include/.
# Pass that path to downstream targets (stack, ddup) so their compilers can
# find the headers without each having to re-run FindLibNative.
cmake_args+=("-DDatadog_INCLUDE_DIRS=${BUILD_DIR}/dd_wrapper/include")

# Run cmake
for target in "${targets[@]}"; do
  run_cmake $target
done
