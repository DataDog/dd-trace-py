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
        '*.cc'
        '*.hpp'
    )

    if [[ "$ENUM_ALL" == "true" ]]; then
        local find_conditions=()
        for ext in "${extensions[@]}"; do
            find_conditions+=("-o" "-name" "$ext")
        done
        find_conditions=("${find_conditions[@]:1}")  # Remove first -o
        find "$BASE_DIR" -type f \( "${find_conditions[@]}" \)
    else
        # Only check modified files (staged, unstaged, and committed in current branch vs main)
        {
            git diff --name-only "$(git merge-base origin/main HEAD)"
            git diff --name-only --cached
            git diff --name-only
        } | sort -u | grep -E '\.(c|h|cpp|cc|hpp)$' || true
    fi
}

# Script defaults
UPDATE_MODE=false
ENUM_ALL=false
BASE_DIR=$(dirname "$(dirname "$(realpath "$0")")")
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
    shift
done

# Environment variable overrides
[[ -n "${CFORMAT_FIX:-}" ]] && UPDATE_MODE=true
[[ -n "${CFORMAT_ALL:-}" ]] && ENUM_ALL=true
[[ -n "${CFORMAT_BIN:-}" ]] && CLANG_FORMAT="$CFORMAT_BIN"

if [[ "$UPDATE_MODE" == "true" ]]; then
    # Update mode: Format files in-place
    enumerate_files \
        | exclude_patterns \
        | while IFS= read -r file; do
            # Skip files that don't exist (e.g., deleted but still in git ls-files)
            if [[ ! -f "$file" ]]; then
                continue
            fi
            echo "Formatting $file";
            ${CLANG_FORMAT} -i "$file"
        done
else
    # Check mode: Compare formatted output to existing files
    has_diff=0
    while IFS= read -r filename; do
        # Skip files that don't exist (e.g., deleted but still in git ls-files)
        if [[ ! -f "$filename" ]]; then
            continue
        fi
        CFORMAT_TMP=$(mktemp)
        ${CLANG_FORMAT} "$filename" > "$CFORMAT_TMP"
        if ! diff -u "$filename" "$CFORMAT_TMP"; then
            has_diff=1
        fi
        rm -f "$CFORMAT_TMP"
    done < <(enumerate_files | exclude_patterns)
    exit $has_diff
fi

