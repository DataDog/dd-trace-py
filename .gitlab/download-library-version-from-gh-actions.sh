#!/bin/bash
set -eo pipefail

source .gitlab/gha-utils.sh
RUN_ID=$(wait_for_run_id)

timeout=600 # 10 minutes
start_time=$(date +%s)
end_time=$((start_time + timeout))
# Loop for 10 minutes waiting for run to appear in github
while [ $(date +%s) -lt $end_time ]; do
  # If the artifact isn't ready yet, then the download will fail
  if gh run download $RUN_ID --repo DataDog/dd-trace-py --pattern "library-version"; then
    break
  fi

  echo "Waiting for library version"
  sleep 30
done

echo "Library Version: $(cat library-version/version.txt)"
