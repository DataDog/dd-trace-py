#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(py|pyx|pxd)$' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    file_count=$(echo "$staged_files" | wc -w | tr -d ' ')
    echo "Checking $file_count staged Python/Cython file(s) with ruff (--fix)..."
    hatch -v run lint:ruff-fix -- $staged_files
else
    echo 'ruff check skipped: No Python/Cython files were found in `git diff --staged`'
fi
