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
    [[ "$f" == "ddtrace/.gitlab-ci.yml" ]] && return 0
    [[ "$f" == ddtrace/.gitlab/* ]] && return 0
    [[ "$f" == ddtrace/.cursor/* ]] && return 0
    [[ "$f" == ddtrace/.claude/* ]] && return 0
    [[ "$f" == ddtrace/docs/* ]] && return 0
    [[ "$f" == ddtrace/releasenotes/* ]] && return 0
    [[ "$f" == ddtrace/*.md ]] && return 0
    [[ "$f" == ddtrace/*.rst ]] && return 0
    [[ "$f" == ddtrace/LICENSE* ]] && return 0
    [[ "$f" == "ddtrace/NOTICE" ]] && return 0
    [[ "$f" == ddtrace/tests/* ]] && return 0
    [[ "$f" == ddtrace/benchmarks/* ]] && return 0
    [[ "$f" == "ddtrace/.gitignore" ]] && return 0
    [[ "$f" == "ddtrace/.gitattributes" ]] && return 0
    [[ "$f" == ddtrace/hooks/* ]] && return 0
    # scripts/ — only ignore subdirectories that are clearly CI/dev tooling.
    # Individual script files are NOT ignored since some affect wheel builds
    # (e.g. zip_filter.py, validate_wheel.py, download-s3-wheels.sh).
    [[ "$f" == ddtrace/scripts/ci-analysis/* ]] && return 0
    [[ "$f" == ddtrace/scripts/ci_visibility/* ]] && return 0
    [[ "$f" == ddtrace/scripts/docs/* ]] && return 0
    [[ "$f" == ddtrace/scripts/iast/* ]] && return 0
    [[ "$f" == ddtrace/scripts/import-analysis/* ]] && return 0
    [[ "$f" == ddtrace/scripts/integration_registry/* ]] && return 0
    [[ "$f" == ddtrace/scripts/tested_versions/* ]] && return 0
    [[ "$f" == ddtrace/scripts/profiles/* ]] && return 0
    [[ "$f" == ddtrace/scripts/trace_flares/* ]] && return 0
    [[ "$f" == ddtrace/scripts/vcr/* ]] && return 0
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
