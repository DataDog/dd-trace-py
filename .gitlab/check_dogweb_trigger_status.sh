#!/bin/bash

set -e

GITLAB_TOKEN=$(aws ssm get-parameter \
    --region us-east-1 \
    --name "dogweb-read-api" \
    --with-decryption \
    --query "Parameter.Value" \
    --out text)

URL="$CI_API_V4_URL/projects/$CI_PROJECT_ID/pipelines/$CI_PIPELINE_ID/bridges"
TRIGGER_JOBS=$(curl $URL --header "PRIVATE-TOKEN: $GITLAB_TOKEN")

for trigger_job in $(echo "${TRIGGER_JOBS}" | jq -r '.[] | @base64'); do
    trigger_job_name=$(echo "${trigger_job}" | base64 --decode | jq -r '.name')
    if [ "${trigger_job_name}" = "dogfood-dogweb-trigger" ]; then
        trigger_job_status=$(echo ${trigger_job} | base64 --decode | jq -r '.status')

        if [ "${trigger_job_status}" = "failed" ]; then
            trigger_job_pipeline_url=$(echo ${trigger_job} | base64 --decode | jq -r '.downstream_pipeline.web_url')
            echo "The dogfood-dogweb-trigger job failed, see what went wrong here: ${trigger_job_pipeline_url}"
            exit 1
        fi
    fi
done
