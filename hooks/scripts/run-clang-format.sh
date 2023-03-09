#!/usr/bin/env sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.cpp$')
if [ -n "$staged_files" ]; then
    clang-format -i $staged_files
else
    echo 'Run clang-format skipped: No C++ files were found in `git diff --staged`'
fi
