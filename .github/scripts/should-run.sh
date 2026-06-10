#!/usr/bin/env bash
#
# Shared CI gate: determines whether a PR contains changes that require
# running a given workflow. Outputs "run=true" or "run=false" to $GITHUB_OUTPUT.
#
# Usage (in a GitHub Actions step):
#   .github/scripts/should-run.sh "${{ github.event_name }}" \
#       "${{ github.event.pull_request.base.sha }}"
#
# Non-PR events always return run=true.
# For PRs, returns run=false when ALL changed files match the ignore list.

set -euo pipefail

EVENT_NAME="${1:?Usage: should-run.sh <event_name> <base_sha>}"
BASE_SHA="${2:-}"

# Non-PR events (push, schedule, workflow_dispatch) always run
if [ "$EVENT_NAME" != "pull_request" ]; then
    echo "run=true" >> "$GITHUB_OUTPUT"
    exit 0
fi

changed=$(git diff --name-only "$BASE_SHA" HEAD)

is_ignored() {
    local f="$1"
    [[ "$f" == ".gitlab-ci.yml" ]] && return 0
    [[ "$f" == .gitlab/* ]] && return 0
    [[ "$f" == .cursor/* ]] && return 0
    [[ "$f" == .claude/* ]] && return 0
    [[ "$f" == docs/* ]] && return 0
    [[ "$f" == releasenotes/* ]] && return 0
    [[ "$f" == *.md ]] && return 0
    [[ "$f" == *.rst ]] && return 0
    [[ "$f" == LICENSE* ]] && return 0
    [[ "$f" == "NOTICE" ]] && return 0
    [[ "$f" == tests/* ]] && return 0
    [[ "$f" == benchmarks/* ]] && return 0
    [[ "$f" == ".gitignore" ]] && return 0
    [[ "$f" == ".gitattributes" ]] && return 0
    [[ "$f" == hooks/* ]] && return 0
    # scripts/ — only ignore subdirectories that are clearly CI/dev tooling.
    # Individual script files are NOT ignored since some affect wheel builds
    # (e.g. zip_filter.py, validate_wheel.py, download-s3-wheels.sh).
    [[ "$f" == scripts/ci-analysis/* ]] && return 0
    [[ "$f" == scripts/ci_visibility/* ]] && return 0
    [[ "$f" == scripts/docs/* ]] && return 0
    [[ "$f" == scripts/iast/* ]] && return 0
    [[ "$f" == scripts/import-analysis/* ]] && return 0
    [[ "$f" == scripts/integration_registry/* ]] && return 0
    [[ "$f" == scripts/profiles/* ]] && return 0
    [[ "$f" == scripts/trace_flares/* ]] && return 0
    [[ "$f" == scripts/vcr/* ]] && return 0
    return 1
}

run=false
while IFS= read -r file; do
    if ! is_ignored "$file"; then
        run=true
        break
    fi
done <<< "$changed"

echo "run=$run" >> "$GITHUB_OUTPUT"
