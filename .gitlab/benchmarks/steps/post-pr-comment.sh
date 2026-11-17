#!/usr/bin/env bash
set -e -o pipefail

if [ -f "$REPORTS_DIR/fail-on-breach.md" ]; then
    format-benchmark-results.py | pr-commenter --for-repo="$UPSTREAM_PROJECT_NAME" --for-pr="$UPSTREAM_BRANCH" --header="Performance SLOs" --on-duplicate=replace
else
    # TODO: Remove me once we no longer have threshold checks in active branches
    post-pr-comment-old.sh
fi
