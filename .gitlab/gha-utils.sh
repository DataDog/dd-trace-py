#!/bin/bash
set -eo pipefail

wait_for_run_id() {
  get_run_id() {
      RUN_ID=$(
          gh run ls \
          --repo DataDog/dd-trace-py \
          --commit="$CI_COMMIT_SHA" \
          $([ -z "$TRIGGERING_EVENT" ] && echo "" || echo "--event=$TRIGGERING_EVENT") \
          --workflow=build_deploy.yml \
          --json databaseId \
          --jq "first (.[]) | .databaseId"
      )
  }

  if [ -z "$CI_COMMIT_SHA" ]; then
    echo "Error: CI_COMMIT_SHA was not provided" >&2
    exit 1
  fi

  if [ -v CI_COMMIT_TAG ]; then
      TRIGGERING_EVENT="release"
  fi

  get_run_id

  if [ -z "$RUN_ID" ]; then
    echo "No RUN_ID found waiting for job to start" >&2
    # The job has not started yet. Give it time to start
    sleep 30

    echo "Querying for RUN_ID" >&2

    timeout=600 # 10 minutes
    start_time=$(date +%s)
    end_time=$((start_time + timeout))
    # Loop for 10 minutes waiting for run to appear in github
    while [ $(date +%s) -lt $end_time ]; do
      get_run_id
      if [ -n "$RUN_ID" ]; then
        break;
      fi
      echo "Waiting for RUN_ID" >&2
      sleep 30
    done
  fi

  if [ -z "$RUN_ID" ]; then
    echo "RUN_ID not found. Check if the GitHub build jobs were successfully triggered on your PR. Usually closing and re-opening your PR will resolve this issue." >&2
    exit 1
  fi

  echo "Found RUN_ID: $RUN_ID" >&2

  echo "${RUN_ID}"
}
