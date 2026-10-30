#!/bin/bash
set -eo pipefail

source .gitlab/gha-utils.sh

RUN_ID=$(wait_for_run_id)

mkdir pywheels
cd pywheels

if [[ $(gh_retry run view $RUN_ID --repo "$GH_REPO" --json status --jq .status) != "completed" ]]; then
  echo "Waiting for workflow to finish"

  # Give time to the job to finish
  sleep 300 # 5 minutes
fi

# gh run watch returns at once for a completed run. Its exit status is the only
# check that fails this job when the workflow run failed.
gh_watch_run "$GH_REPO" $RUN_ID --interval 60 --exit-status

echo "Github workflow finished. Downloading wheels"
# Download only win_arm64 wheels; amd64 and x86 wheels are built directly in GitLab.
# Retry on transient HTTP errors (e.g. 503 egress limit).
MAX_RETRIES=5
RETRY_DELAY=5
for attempt in $(seq 1 $MAX_RETRIES); do
  # Clean any partial results so gh run download can re-extract with O_EXCL
  rm -rf ./*
  if gh run download $RUN_ID --repo "$GH_REPO" --pattern "wheels-*-win_arm64"; then
    break
  fi
  if [[ $attempt -eq $MAX_RETRIES ]]; then
    echo "Failed to download wheels after $MAX_RETRIES attempts"
    exit 1
  fi
  echo "Download attempt $attempt failed, retrying in ${RETRY_DELAY}s..."
  sleep $RETRY_DELAY
  RETRY_DELAY=$((RETRY_DELAY * 2))
done

cd ..

echo "Finished downloading wheels. Fixing directory structure"

# Flatten directory structure so all wheels are top level
find pywheels -type f -exec mv {} pywheels \;

echo "Done"
