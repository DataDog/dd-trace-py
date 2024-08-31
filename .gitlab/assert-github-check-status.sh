#!/usr/bin/env bash
set -eo pipefail

WORKFLOW_NAME="${1}"
if [ -z "$WORKFLOW_NAME" ]; then
  echo "Error: WORKFLOW_NAME was not provided"
  exit 1
fi

if [ -z "$CI_COMMIT_SHA" ]; then
  echo "Error: CI_COMMIT_SHA was not provided"
  exit 1
fi

echo "Querying for RUN_ID"
timeout=600 # 10 minutes
start_time=$(date +%s)
end_time=$((start_time + timeout))
# Loop for 10 minutes waiting for run to appear in github
while [ $(date +%s) -lt $end_time ]; do
  RUN_ID=$(gh run ls --repo DataDog/dd-trace-py --commit=$CI_COMMIT_SHA --workflow=${WORKFLOW_NAME} --json databaseId --jq "first (.[]) | .databaseId")
  if [ -n "$RUN_ID" ]; then
    break;
  fi
  echo "Waiting for RUN_ID"
  sleep 20
done

if [ -z "$RUN_ID" ]; then
  echo "RUN_ID not found"
  exit 1
fi

echo "Found RUN_ID: $RUN_ID"
echo "Waiting for workflow to finish"
# wait for run to finish
gh run watch $RUN_ID --interval 45 --exit-status 1 --repo DataDog/dd-trace-py

# Get workflow result
RESULT=$(gh run view $RUN_ID --json conclusion,status)
CONCLUSION=$(echo $RESULT | jq -r .conclusion)
STATUS=$(echo $RESULT | jq -r .status)

if [ "$CONCLUSION" != "success" ]; then
  echo "Workflow failed with status: $STATUS and conclusion: $CONCLUSION"
  exit 1
fi
