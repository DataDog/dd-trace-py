#!/bin/sh
LINT_CMD="${LINT_CMD:-scripts/lint}"
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
if [ -n "$staged_files" ]; then
    "$LINT_CMD" codespell $staged_files
fi
