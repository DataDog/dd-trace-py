#!/bin/sh
LINT_CMD="${LINT_CMD:-scripts/lint}"
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
if [ -n "$staged_files" ]; then
    # Use the "spelling" subcommand so the project's codespell wordlist and skip
    # list apply; the raw "codespell" passthrough fails on known false positives
    # (e.g. the obfuscation regex in docs/configuration.rst).
    "$LINT_CMD" spelling $staged_files
fi
