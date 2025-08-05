#!/usr/bin/env bash
set -e

clean() {
    rm -f "$CFORMAT_TMP" 2>/dev/null || true
}

trap clean EXIT

# Exclude patterns applied to file list
exclude_patterns() {
    local patterns=(
        '^ddtrace/vendor/'
        '^ddtrace/appsec/_iast/_taint_tracking/_vendor/'
        '.eggs/'
        'dd-trace-py/build/'
        '_taint_tracking/CMakeFiles'
        '_taint_tracking/_deps/'
        '.riot/'
        '_taint_tracking/_vendor/'
        'ddtrace/appsec/_iast/_taint_tracking/cmake-build-debug/'
        'ddtrace/profiling/collector/vendor/'
    )

    # Join all patterns with '|'
    local joined="$(IFS='|'; echo "${patterns[*]}")"

    grep -vE "${joined}"
}

# Function to enumerate files depending on mode
enumerate_files() {
    local extensions=(
        '*.c'
        '*.h'
        '*.cpp'
        '*.hpp'
    )

    if [[ "$ENUM_ALL" == "true" ]]; then
        local find_conditions=()
        for ext in "${extensions[@]}"; do
            find_conditions+=("-o" "-name" "$ext")
        done
        unset 'find_conditions[-1]'
        find "$BASE_DIR" -type f \( "${find_conditions[@]}" \)
    else
        git ls-files "${extensions[@]}"
    fi
}

# Script defaults
UPDATE_MODE=false
ENUM_ALL=false
BASE_DIR=$(dirname "$(realpath "$0")")
CLANG_FORMAT=clang-format

# NB: consumes the arguments
while (( "$#" )); do
    case "$1" in
        --fix|-fix|fix)
            UPDATE_MODE="true"
            ;;
        --all|-all|all)
            ENUM_ALL="true"
            ;;
        *)
            ;;
    esac
done

# Environment variable overrides
[[ -n "${CFORMAT_FIX:-}" ]] && UPDATE_MODE=true
[[ -n "${CFORMAT_ALL:-}" ]] && ENUM_ALL=true
[[ -n "${CFORMAT_BIN:-}" ]] && CLANG_FORMAT="$CLANG_FORMAT_BIN"

if [[ "$UPDATE_MODE" == "true" ]]; then
    # Update mode: Format files in-place
    enumerate_files \
        | exclude_patterns \
        | while IFS= read -r file; do
            ${CLANG_FORMAT} -i "$file"
            echo "Formatting $file"
        done
else
    # Check mode: Compare formatted output to existing files
    has_diff=0
    while IFS= read -r filename; do
        CFORMAT_TMP=$(mktemp)
        ${CLANG_FORMAT} "$filename" > "$CFORMAT_TMP"
        if ! diff -u "$filename" "$CFORMAT_TMP"; then
            has_diff=1
        fi
        rm -f "$CFORMAT_TMP"
    done < <(enumerate_files | exclude_patterns)
    exit $has_diff
fi

