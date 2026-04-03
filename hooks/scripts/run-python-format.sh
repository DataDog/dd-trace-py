#!/bin/sh

staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.py$')
if [ -n "$staged_files" ]; then
    # shellcheck disable=SC2086  # Intentional word-splitting: $staged_files is a space-separated list of filenames
    hatch -v run lint:format_check $staged_files
else
    # shellcheck disable=SC2016  # Backtick in message is literal, not a command substitution
    echo 'hatch style check skipped: No Python files were found in `git diff --staged`'
fi
