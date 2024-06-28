#!/bin/bash
set -eo pipefail

if [ -z "$CI_COMMIT_SHA" ]; then
  echo "Error: CI_COMMIT_SHA was not provided"
  exit 1
fi

#Hardcoded for testing
CI_COMMIT_SHA=17bc7dadfeed4823c8460bfb0041cec3355fcd2f

RUN_ID=$(gh run ls --repo DataDog/dd-trace-py --commit=$CI_COMMIT_SHA --workflow=build_deploy.yml --json databaseId --jq "first (.[]) | .databaseId")

# wait for run to finish
gh run watch $RUN_ID --interval 15 --exit-status 1 --repo DataDog/dd-trace-py

mkdir pywheels
cd pywheels

# download all wheels
gh run download $RUN_ID --repo DataDog/dd-trace-py

cd ..

# Flatten directory structure so all wheels are top level
find pywheels -type f -exec mv {} pywheels \;
