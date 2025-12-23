#!/bin/bash
# shellcheck disable=SC2086
set -euo pipefail

# Make the go binaries available.
# It is used by CodeQL to build the Go language DB, and our own Go script that pushes results to GitHub.
export PATH=$PATH:/usr/local/go/bin

# Clone Code Scanning repository to download custom CodeQL packs from.
git config --global url."https://gitlab-ci-token:${CI_JOB_TOKEN}@gitlab.ddbuild.io/DataDog/".insteadOf "https://github.com/DataDog/"
git clone https://github.com/DataDog/codescanning.git --depth 1 --single-branch --branch=main /tmp/codescanning

dd-octo-sts debug --scope DataDog/dd-trace-py --policy codeql || true

# Create CodeQL databases.
$CODEQL database create "$CODEQL_DB" $DB_CONFIGS

# Run queries for each supported ecosystem and save results to intermediate SARIF files.
$CODEQL database analyze "$CODEQL_DB" "$PYTHON_CUSTOM_QLPACK" $SCAN_CONFIGS --sarif-category="python" --output="/tmp/python.sarif"

set +x # Disable command echoing to prevent token leakage
export GITHUB_TOKEN="$(DD_TRACE_ENABLED=false dd-octo-sts token --scope DataDog/dd-trace-py --policy codeql)"
set -x # Re-enable command echoing
cd /tmp/codescanning && go build -o codescanning_binary && chmod +x codescanning_binary
CODEQL_SARIF="/tmp/python.sarif" ./codescanning_binary -upload_sarif=true -scan_started_time="${CI_JOB_STARTED_AT}"
