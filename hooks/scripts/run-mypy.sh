#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.py$')
if [ -n "$staged_files" ]; then
    hatch -v run lint:typing
else
    echo 'Run mypy skipped: No Python files were found in `git diff --staged`'
fi
