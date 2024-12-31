#!/usr/bin/env bash
set -e

# Script defaults
UPDATE_MODE=false
ENUM_ALL=false
CMD_OPT="--check"
BASE_DIR=$(dirname "$(realpath "$0")")
CMAKE_FORMAT="cmake-format"

# NB: consumes the arguments
while (( "$#" )); do
    case "$1" in
        --fix|-fix|fix)
            UPDATE_MODE=true
            ;;
        --all|-all|all)
            ENUM_ALL=true
            ;;
        *)
            ;;
    esac
    shift
done

# Environment variable overrides
[[ -n "${CMAKE_FORMAT_FIX:-}" || "$UPDATE_MODE" == "true" ]] && CMD_OPT="--in-place"
[[ -n "${CMAKE_FORMAT_ALL:-}" ]] && ENUM_ALL=true
[[ -n "${CMAKE_FORMAT_BIN:-}" ]] && CMAKE_FORMAT="$CMAKE_FORMAT_BIN"

# Enumerate files function
enumerate_files() {
    if [[ "$ENUM_ALL" == true ]]; then
        find $BASE_DIR \( -name '*.cmake' -o -name 'CMakeLists.txt' \)
    else
        git ls-files \
            | grep -E '\.cmake$|CMakeLists.txt' || true
    fi
}

# Enumerate and filter files
FILES=$(enumerate_files | grep -vE '^(\./)?build/' | grep -vE '_vendor/')

# Run cmake-format on all files
# Use a process substitution to allow iterating safely
while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    ${CMAKE_FORMAT} -c "scripts/.cmake-format" $CMD_OPT "$file"
done <<< "$FILES"

