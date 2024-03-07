#!/bin/bash
set -euox pipefail

### Useful globals
MY_DIR=$(dirname $(realpath $0))
MY_NAME="$0"
BUILD_DIR="build"

### Compiler discovery
# Initialize variables to store the highest versions
highest_gcc=""
highest_gxx=""
highest_clang=""
highest_clangxx=""

# Function to find the highest version of compilers
find_highest_compiler_version() {
  local base_name=$1
  local highest_var_name=$2
  local highest_version=0

  # Try to find the latest versions of both GCC and Clang
  # The range 5-20 is arbitrary (GCC 5 was released in 2015, and 20 is just a
  # a high number since Clang is on version 17)
  # TODO provide a passthrough in CI where we want to use a pinned or specified
  # version. Not a big deal since this script is currently only used for local
  # development.
  for version in {20..5}; do
    if command -v "${base_name}-${version}" &> /dev/null; then
      if [ $version -gt $highest_version ]; then
        highest_version=$version
        eval "$highest_var_name=${base_name}-${version}"
      fi
    fi
  done

  # Check for the base version if no numbered version was found
  if [ $highest_version -eq 0 ] && command -v "$base_name" &> /dev/null; then
    eval "$highest_var_name=$base_name"
  fi
}

# Find highest versions for each compiler
find_highest_compiler_version gcc highest_gcc
find_highest_compiler_version g++ highest_gxx
find_highest_compiler_version clang highest_clang
find_highest_compiler_version clang++ highest_clangxx

### Build setup
# Targets to target dirs
declare -A target_dirs
target_dirs["ddup"]="ddup"
target_dirs["stack_v2"]="stack_v2"
target_dirs["dd_wrapper"]="dd_wrapper"

# Compiler options
declare -A compiler_args
compiler_args["safety"]="-DSANITIZE_OPTIONS=address,leak,undefined"
compiler_args["thread"]="-DSANITIZE_OPTIONS=thread"
compiler_args["numerical"]="-DSANITIZE_OPTIONS=integer,nullability,signed-integer-overflow,bounds,float-divide-by-zero"
compiler_args["dataflow"]="-DSANITIZE_OPTIONS=dataflow"
compiler_args["memory"]="-DSANITIZE_OPTIONS=memory"
compiler_args["fanalyzer"]="-DDO_FANALYZE=ON"
compiler_args["cppcheck"]="-DDO_CPPCHECK=ON"

# Initial cmake args
cmake_args=(
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  -DCMAKE_VERBOSE_MAKEFILE=ON
  -DLIB_INSTALL_DIR=$(realpath $MY_DIR)/lib
  -DPython3_INCLUDE_DIRS=$(python3 -c "from distutils.sysconfig import get_python_inc; print(get_python_inc())")
  -DPython3_EXECUTABLE=$(which python3)
  -DPython3_LIBRARY=$(python3 -c "import distutils.sysconfig as sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
)

# Initial build targets; no matter what, dd_wrapper is the base dependency, so it's always built
targets=("dd_wrapper")

# Helper functions for finding the compiler(s)
set_clang() {
  export CC=$highest_clang
  export CXX=$highest_clangxx
  cmake_args+=(
    -DCMAKE_C_COMPILER=$CC
    -DCMAKE_CXX_COMPILER=$CXX
  )
}

set_gcc() {
  export CC=$highest_gcc
  export CXX=$highest_gxx
  cmake_args+=(
    -DCMAKE_C_COMPILER=$CC
    -DCMAKE_CXX_COMPILER=$CXX
  )
}

### Build runners
run_cmake() {
  target=$1
  dir=${target_dirs[$target]}
  if [ -z "$dir" ]; then
    echo "No directory specified for cmake"
    exit 1
  fi

  # Enter the directory and create the build directory
  pushd $dir || { echo "Failed to change to $dir"; exit 1; }
  mkdir -p $BUILD_DIR && cd $BUILD_DIR || { echo "Failed to create build directory for $dir"; exit 1; }

  # Run cmake
  cmake "${cmake_args[@]}" .. || { echo "cmake failed"; exit 1; }
  cmake --build . || { echo "build failed"; exit 1; }
  if [[ " ${cmake_args[*]} " =~ " -DDO_CPPCHECK=ON " ]]; then
    echo "--------------------------------------------------------------------- Running CPPCHECK"
    make cppcheck || { echo "cppcheck failed"; exit 1; }
  fi
  if [[ " ${cmake_args[*]} " =~ " -DBUILD_TESTING=ON " ]]; then
    echo "--------------------------------------------------------------------- Running Tests"
#    make test || { echo "tests failed"; exit 1; }
    ctest --output-on-failure || { echo "tests failed!"; exit 1; }
  fi

  # OK, the build or whatever went fine I guess.
  popd
}

### Print help
print_help() {
  echo "Usage: ${MY_NAME} [options] [build_mode] [target]"
  echo "Options (one of)"
  echo "  -h, --help        Show this help message and exit"
  echo "  -s, --safety      Clang + " ${compile_args["safety"]}
  echo "  -t, --thread      Clang + " ${compile_args["thread"]}
  echo "  -n, --numerical   Clang + " ${compile_args["numerical"]}
  echo "  -d, --dataflow    Clang + " ${compile_args["dataflow"]}
  echo "  -m  --memory      Clang + " ${compile_args["memory"]}
  echo "  -C  --cppcheck    Clang + " ${compile_args["cppcheck"]}
  echo "  -f, --fanalyze    GCC + " ${compile_args["fanalyzer"]}
  echo "  -c, --clang       Clang (alone)"
  echo "  -g, --gcc         GCC (alone)"
  echo "  --                Don't do anything special"
  echo ""
  echo "Build Modes:"
  echo "  Debug (default)"
  echo "  Release"
  echo "  RelWithDebInfo"
  echo ""
  echo "(any possible others, depending on what cmake supports for"
  echo "BUILD_TYPE out of the box)"
  echo ""
  echo "Targets:"
  echo "  all"
  echo "  all_test (default)"
  echo "  dd_wrapper"
  echo "  dd_wrapper_test"
  echo "  stack_v2 (also builds dd_wrapper)"
  echo "  stack_v2_test (also builds dd_wrapper_test)"
  echo "  ddup (also builds dd_wrapper)"
  echo "  ddup_test (also builds dd_wrapper_test)"
}

print_cmake_args() {
  echo "CMake Args: ${cmake_args[*]}"
  echo "Targets: ${targets[*]}"
}

### Check input
# Check the first slot, options
add_compiler_args() {
  case "$1" in
    -h|--help)
      print_help
      exit 0
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
    -C|--cppcheck)
      cmake_args+=(${compiler_args["cppcheck"]})
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
      set_gcc # Default to GCC, since this is what will happen in the official build
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
      targets+=("stack_v2")
      targets+=("ddup")
      ;;
    dd_wrapper)
      # We always build dd_wrapper, so no need to add it to the list
      ;;
    stack_v2)
      targets+=("stack_v2")
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


### ENTRYPOINT
# Check for basic input validity
if [ $# -eq 0 ]; then
  echo "No arguments given.  At least one is needed, otherwise I'd (a m b i g u o u s l y) do a lot of work!"
  print_help
  exit 1
fi

add_compiler_args "$1"
add_build_mode "$2"
add_target "$3"

# Print cmake args
print_cmake_args

# Run cmake
for target in "${targets[@]}"; do
  run_cmake $target
done
