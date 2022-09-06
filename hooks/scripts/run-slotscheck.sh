#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.py$')
if [ -n "$staged_files" ]; then
    riot -v run -s slotscheck $staged_files
else
    echo 'Run slotscheck skipped: No Python files were found in git diff --staged'
fi
