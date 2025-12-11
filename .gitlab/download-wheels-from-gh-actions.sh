#!/bin/bash
set -eo pipefail

# Optional: accept commit SHA as first argument, otherwise use CI_COMMIT_SHA
COMMIT_SHA="${1:-${CI_COMMIT_SHA}}"
OUTPUT_DIR="${2:-pywheels}"

if [ -z "${COMMIT_SHA}" ]; then
  echo "Error: Commit SHA must be provided as argument or CI_COMMIT_SHA must be set" >&2
  exit 1
fi

source .gitlab/gha-utils.sh

# Temporarily override CI_COMMIT_SHA to get run ID for the specified commit
ORIGINAL_CI_COMMIT_SHA="${CI_COMMIT_SHA:-}"
export CI_COMMIT_SHA="${COMMIT_SHA}"

RUN_ID=$(wait_for_run_id)

# Restore original CI_COMMIT_SHA (or unset if it wasn't set originally)
if [ -n "${ORIGINAL_CI_COMMIT_SHA}" ]; then
  export CI_COMMIT_SHA="${ORIGINAL_CI_COMMIT_SHA}"
else
  unset CI_COMMIT_SHA
fi

mkdir -p "${OUTPUT_DIR}"
cd "${OUTPUT_DIR}"

if [[ $(gh run view $RUN_ID --exit-status --json status --jq .status) != "completed" ]]; then
  echo "Waiting for workflow to finish"

  # Give time to the job to finish
  sleep 300 # 5 minutes

  # wait for run to finish
  gh run watch $RUN_ID --interval 60 --exit-status 1 --repo DataDog/dd-trace-py
fi

echo "Github workflow finished. Downloading wheels"
# download all wheels
gh run download $RUN_ID --repo DataDog/dd-trace-py --pattern "wheels-*" --pattern "source-dist*"

cd ..

echo "Finished downloading wheels. Fixing directory structure"

# Flatten directory structure so all wheels are top level
find "${OUTPUT_DIR}" -type f -exec mv {} "${OUTPUT_DIR}" \;

echo "Done"
