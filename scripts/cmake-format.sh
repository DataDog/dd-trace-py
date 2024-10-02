#!/bin/bash
set -e

# Navigate to the root of the repository, which is one level up from the directory containing this script.
SCRIPT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$SCRIPT_ROOT/.."

# Set some options for cmake-format
# If --update is passed as first arg, or if CMAKE_FORMAT_FIX_ALL is set, update the files in place
CMD_OPT="--check"
if [[ "${1:-}" == "--update" || -n "${CMAKE_FORMAT_FIX_ALL:-}" ]]; then
  CMD_OPT="--in-place"
fi

# If the CMAKE_FORMAT_CHECK_ALL environnment variable is truthy, check all files
# else, just check the files that have been modified
if [[ -n "${CMAKE_FORMAT_CHECK_ALL:-}" ]]; then
  FILES=$(find . -name '*.cmake' -o -name 'CMakeLists.txt' | grep -vE '^./build/' | grep -vE '_vendor/')
else
  FILES=$(git diff --name-only HEAD | grep -E '\.cmake$|CMakeLists.txt' | grep -vE '^build/' | grep -vE '_vendor/' || true)
fi

# Run cmake-format on all files
for file in $FILES; do
  cmake-format -c "scripts/.cmake-format" $CMD_OPT "$file"
done
