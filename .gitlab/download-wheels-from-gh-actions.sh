#!/bin/bash

if [ -z "$CI_COMMIT_SHA" ]; then
  echo "Error: CI_COMMIT_SHA was not provided"
  exit 1
fi

RUN_ID=$(gh run ls --repo DataDog/dd-trace-py --commit=$CI_COMMIT_SHA --workflow=build_deploy.yml --json databaseId --jq "first (.[]) | .databaseId")

# wait for run to finish
gh run watch 9699662933 --interval 15 --exit-status 1 --repo DataDog/dd-trace-py

mkdir pywheels
cd pywheels

# download all wheels
gh run download $RUN_ID --repo DataDog/dd-trace-py

cd ..

# Flatten directory structure so all wheels are top level
find pywheels -type f -exec mv {} pywheels \;
