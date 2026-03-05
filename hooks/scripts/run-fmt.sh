#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.py$' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    hatch -v run lint:fmt -- $staged_files
else
    echo 'hatch lint:fmt skipped: No Python files were found in `git diff --staged`'
fi
