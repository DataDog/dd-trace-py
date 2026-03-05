#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(py|pyi)$' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    hatch -v run lint:typing -- $staged_files
else
    echo 'Run mypy skipped: No Python/stub files were found in `git diff --staged`'
fi
