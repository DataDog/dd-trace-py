#!/bin/bash
set -eo pipefail

CI_COMMIT_SHA="ab06d5ccecb5952dd7cd7df3f787b62f5c017f89"

if [ -z "$CI_COMMIT_SHA" ]; then
  echo "Error: CI_COMMIT_SHA was not provided"
  exit 1
fi

RUN_ID=$(gh run ls --repo DataDog/dd-trace-py --commit=$CI_COMMIT_SHA --workflow=build_deploy.yml --json databaseId --jq "first (.[]) | .databaseId")
if [ -z "$RUN_ID" ]; then
  echo "No RUN_ID found waiting for job to start"
  # The job has not started yet. Give it time to start
  sleep 180 # 3 minutes

  echo "Querying for RUN_ID"

  timeout=600 # 10 minutes
  start_time=$(date +%s)
  end_time=$((start_time + timeout))
  # Loop for 10 minutes waiting for run to appear in github
  while [ $(date +%s) -lt $end_time ]; do
    RUN_ID=$(gh run ls --repo DataDog/dd-trace-py --commit=$CI_COMMIT_SHA --workflow=build_deploy.yml --json databaseId --jq "first (.[]) | .databaseId")
    if [ -n "$RUN_ID" ]; then
      break;
    fi
    echo "Waiting for RUN_ID"
    sleep 60
  done
fi

if [ -z "$RUN_ID" ]; then
  echo "RUN_ID not found. Check if the GitHub build jobs were successfully triggered on your PR. Usually closing and re-opening your PR will resolve this issue."
  exit 1
fi

echo "Found RUN_ID: $RUN_ID"

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
# download all wheels
gh run download $RUN_ID --repo DataDog/dd-trace-py

cd ..

echo "Finished downloading wheels. Fixing directory structure"

# Flatten directory structure so all wheels are top level
find pywheels -type f -exec mv {} pywheels \;

echo "Done"
