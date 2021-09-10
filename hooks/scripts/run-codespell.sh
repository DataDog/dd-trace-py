#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
if [ -n "$staged_files" ]; then
    riot -v run -s hook-codespell $staged_files
fi
