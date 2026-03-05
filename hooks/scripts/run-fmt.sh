#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.py$' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    file_count=$(echo "$staged_files" | wc -w | tr -d ' ')
    echo "Formatting and linting $file_count staged Python file(s)..."
    hatch -v run lint:pre-commit-python -- $staged_files
    exit_code=$?
    if [ $exit_code -eq 0 ]; then
        # Re-stage files ruff may have modified so fixes are included in the commit
        echo "$staged_files" | tr ' ' '\n' | while read -r f; do
            [ -n "$f" ] && git add "$f"
        done
    fi
    exit $exit_code
else
    echo 'Format/lint skipped: No Python files were found in `git diff --staged`'
fi
