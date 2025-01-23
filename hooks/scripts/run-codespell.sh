#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
if [ -n "$staged_files" ]; then
    hatch -v run lint:spelling $staged_files
fi
