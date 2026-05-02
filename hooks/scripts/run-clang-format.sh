#!/usr/bin/env sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(c|h|cc|cpp|hpp)$')
if [ -n "$staged_files" ]; then
    # shellcheck disable=SC2086  # Intentional word-splitting: $staged_files is a space-separated list of filenames
    clang-format -i $staged_files
else
    # shellcheck disable=SC2016  # Backtick in message is literal, not a command substitution
    echo 'Run clang-format skipped: No C/C++ files were found in `git diff --staged`'
fi
