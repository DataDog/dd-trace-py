#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(py|pyx|pxd)$' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    file_count=$(echo "$staged_files" | wc -w | tr -d ' ')
    echo "Checking $file_count staged Python/Cython file(s) with ruff (--fix)..."
    hatch -v run lint:ruff-fix -- $staged_files
    ruff_exit=$?
    if [ $ruff_exit -eq 0 ]; then
        # Re-stage files ruff may have modified so fixes are included in the commit
        echo "$staged_files" | tr ' ' '\n' | while read -r f; do
            [ -n "$f" ] && git add "$f"
        done
    fi
    exit $ruff_exit
else
    echo 'ruff check skipped: No Python/Cython files were found in `git diff --staged`'
fi
