#!/bin/bash

# This script is used to build the library independently of the setup.py
# build system.  This is mostly used to integrate different analysis tools
# which I haven't promoted into our normal builds yet.


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
SANITIZE_OPTIONS="" # passed directly to cmake
SAFETY_OPTIONS="address,leak,undefined"
THREAD_OPTIONS="thread"
NUMERICAL_OPTIONS="integer,nullability,signed-integer-overflow,bounds,float-divide-by-zero"
DATAFLOW_OPTIONS="dataflow"
MEMORY_OPTIONS="memory"
ANALYZE_OPTIONS="-fanalyzer"

# helper function for setting the compiler
# Final cmake args
cmake_args=(
  -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  -DCMAKE_VERBOSE_MAKEFILE=ON
  -DBUILD_TESTING=ON
)

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



# Check input
if [ -n "$1" ]; then
  case "$1" in
    -h|--help)
      echo "Usage: $0 [options] [build_mode]"
      echo "Options (one of)"
      echo "  -h, --help        Show this help message and exit"
      echo "  -s, --safety      Clang + fsanitize=$SAFETY_OPTIONS"
      echo "  -t, --thread      Clang + fsanitize=$THREAD_OPTIONS"
      echo "  -n, --numerical   Clang + fsanitize=$NUMERICAL_OPTIONS"
      echo "  -d, --dataflow    Clang + fsanitize=$DATAFLOW_OPTIONS"
      echo "  -m  --memory      Clang + fsanitize=$MEMORY_OPTIONS"
      echo "  -C  --cppcheck    Clang + cppcheck"
      echo "  -f, --fanalyze    GCC + -fanalyzer"
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
      echo "-------------------------------------------------"
      echo "Diagnostic parameters"
      echo "Highest gcc: $highest_gcc"
      echo "Highest g++: $highest_gxx"
      echo "Highest Clang: $highest_clang"
      echo "Highest Clang++: $highest_clangxx"
      exit 0
      ;;
    -s|--safety)
      cmake_args+=(-DSANITIZE_OPTIONS=$SAFETY_OPTIONS)
      set_clang
      ;;
    -t|--thread)
      cmake_args+=(-DSANITIZE_OPTIONS=$THREAD_OPTIONS)
      set_clang
      ;;
    -n|--numerical)
      cmake_args+=(-DSANITIZE_OPTIONS=$NUMERICAL_OPTIONS)
      set_clang
      ;;
    -d|--dataflow)
      cmake_args+=(-DSANITIZE_OPTIONS=$DATAFLOW_OPTIONS)
      set_clang
      ;;
    -m|--memory)
      cmake_args+=(-DSANITIZE_OPTIONS=$MEMORY_OPTIONS)
      set_clang
      ;;
    -C|--cppcheck)
      cmake_args+=(-DDO_CPPCHECK=ON)
      set_clang
      ;;
    -f|--fanalyze)
      cmake_args+=(-DDO_FANALYZE=ON)
      set_gcc
      ;;
    -c|--clang)
      set_clang
      ;;
    -g|--gcc)
      set_gcc
      ;;
    --)
      ;;
    *)
      echo "Unknown option: $1"
      exit 1
      ;;
  esac
else
  set_gcc
fi

# If there are two arguments, override build mode
BUILD_MODE=${2:-Debug}
cmake_args+=(-DCMAKE_BUILD_TYPE=$BUILD_MODE)

# Setup cmake stuff
BUILD_DIR="build"
mkdir -p $BUILD_DIR && cd $BUILD_DIR || { echo "Failed to create build directory"; exit 1; }

# Run cmake
cmake "${cmake_args[@]}" .. || { echo "cmake failed"; exit 1; }
cmake --build . || { echo "build failed"; exit 1; }
if [[ "$SANITIZE_OPTIONS" == "cppcheck" ]]; then
  make cppcheck_run || { echo "cppcheck failed"; exit 1; }
fi
ctest --output-on-failure || { echo "tests failed!"; exit 1; }
