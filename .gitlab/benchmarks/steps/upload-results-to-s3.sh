#!/usr/bin/env bash

PROJECT="${UPSTREAM_PROJECT_NAME:-$CI_PROJECT_NAME}"
BRANCH="${UPSTREAM_BRANCH:-$CI_COMMIT_REF_NAME}"

S3_URL=s3://relenv-benchmarking-data/${PROJECT}/${BRANCH}/${CI_JOB_ID}/

aws s3 cp --recursive --acl bucket-owner-full-control /artifacts/${CI_JOB_ID}/ $S3_URL
