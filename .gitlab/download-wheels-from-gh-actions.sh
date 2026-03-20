#!/bin/bash
set -eo pipefail

source .gitlab/gha-utils.sh

RUN_ID=$(wait_for_run_id)

mkdir pywheels
cd pywheels

if [[ $(gh run view $RUN_ID --exit-status --json status --jq .status) != "completed" ]]; then
  echo "Waiting for workflow to finish"

  # Give time to the job to finish
  sleep 300 # 5 minutes

  # wait for run to finish
  gh run watch $RUN_ID --interval 60 --exit-status 1 --repo DataDog/dd-trace-py
fi

echo "Github workflow finished. Downloading wheels"
# Download only win_arm64 wheels; amd64 and x86 wheels are built directly in GitLab.
gh run download $RUN_ID --repo DataDog/dd-trace-py --pattern "wheels-*-win_arm64"

cd ..

echo "Finished downloading wheels. Fixing directory structure"

# Flatten directory structure so all wheels are top level
find pywheels -type f -exec mv {} pywheels \;

echo "Done"
