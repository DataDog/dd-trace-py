#!/bin/bash
set -eo pipefail

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
  RUN_ID=$(gh run ls --repo DataDog/dd-trace-py --commit=$CI_COMMIT_SHA --workflow=build_deploy.yml --json databaseId --jq "first (.[]) | .databaseId")
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

mkdir gh_wheels
cd gh_wheels

echo "Github workflow finished. Downloading wheels"

# download all wheels
gh run download $RUN_ID --repo DataDog/dd-trace-py

cd ..

echo "Finished downloading wheels. Fixing directory structure"

# Flatten directory structure so all wheels are top level
find gh_wheels -type f -exec mv {} gh_wheels \;

# Only keep wheels which we do not already have an artifact for
for f in gh_wheels/*.whl;
do
    name=$(basename $f)
    if [ ! -f ./wheelhouse/$name ];
    then
        echo "Copying $f to wheelhouse"
        cp $f ../wheelhouse
    else
        echo "Skipping $name as it already exists in wheelhouse"
    fi
done

echo "Done"
