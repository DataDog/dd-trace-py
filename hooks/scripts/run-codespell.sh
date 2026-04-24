#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
if [ -n "$staged_files" ]; then
    # shellcheck disable=SC2086  # Intentional word-splitting: $staged_files is a space-separated list of filenames
    hatch -v run lint:spelling $staged_files
fi
