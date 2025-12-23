#!/bin/bash
# shellcheck disable=SC2086
set -euo pipefail

# Make the go binaries available.
# It is used by CodeQL to build the Go language DB, and our own Go script that pushes results to GitHub.
export PATH=$PATH:/usr/local/go/bin

# Clone Code Scanning repository to download custom CodeQL packs from.
git config --global url."https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.ddbuild.io/DataDog/".insteadOf "https://github.com/DataDog/"
set +x # Disable command echoing to prevent token leakage
git clone https://github.com/DataDog/codescanning.git --depth 1 --single-branch --branch=main /tmp/codescanning
set -x # Re-enable command echoing

dd-octo-sts debug --scope DataDog/dd-trace-py --policy codeql || true

# Create CodeQL databases.
$CODEQL database create "$CODEQL_DB" $DB_CONFIGS

# Run queries for each supported ecosystem and save results to intermediate SARIF files.
$CODEQL database analyze "$CODEQL" "$PYTHON_CUSTOM_QLPACK" $SCAN_CONFIGS --sarif-category="python" --output="/tmp/python.sarif"

# Obtain short-lived GitHub token via DD Octo STS for SARIF upload
GITHUB_TOKEN="$(DD_TRACE_ENABLED=false dd-octo-sts token --scope DataDog/dd-trace-py --policy codeql)"
export GITHUB_TOKEN

# Upload results using custom Go command.
GOPRIVATE=github.com/DataDog GOBIN=/usr/local/go/bin go install github.com/DataDog/codescanning@main
CODEQL_SARIF="/tmp/python.sarif" codescanning $UPLOAD_CONFIGS -scan_started_time="$CI_JOB_STARTED_AT"
