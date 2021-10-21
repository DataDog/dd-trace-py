#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.py$')
if [ -n "$staged_files" ]; then
    riot -v run -s flake8 $staged_files
else
    echo 'Run flake8 skipped: No Python files were found in git diff --staged'
fi
