#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(py|pyi)$' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    file_count=$(echo "$staged_files" | wc -w | tr -d ' ')
    echo "Formatting and linting $file_count staged Python/stub file(s)..."
    # Capture files with unstaged changes before ruff; we skip re-adding these to preserve partial staging
    unstaged_before=$(git diff --name-only)
    hatch -v run lint:pre-commit-python -- $staged_files
    exit_code=$?
    if [ $exit_code -eq 0 ]; then
        # Re-stage only files that had no unstaged changes before ruff (preserves partial staging)
        echo "$staged_files" | tr ' ' '\n' | while read -r f; do
            [ -n "$f" ] || continue
            echo "$unstaged_before" | grep -qFx "$f" || git add "$f"
        done
    fi
    exit $exit_code
else
    echo 'Format/lint skipped: No Python/stub files were found in `git diff --staged`'
fi
