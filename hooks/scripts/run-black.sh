#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.py$')
if [ -n "$staged_files" ]; then
    hatch -v run lint:black $staged_files
else
    echo 'Run black skipped: No Python files were found in `git diff --staged`'
fi
