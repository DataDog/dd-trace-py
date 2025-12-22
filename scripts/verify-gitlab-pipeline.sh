#!/bin/bash
set -euo pipefail

# Verify GitLab Pipeline for a target commit
#
# Expected environment variables:
#   TARGET_SHA: Git SHA to check pipeline for
#   GITLAB_REPO: GitLab repository path (group/project)
#   GITLAB_PROJECT_ID: GitLab project ID (numeric)
#   GITLAB_API_V4_URL: GitLab API URL (defaults to GITLAB_SERVER_URL/api/v4)
#   CI_JOB_TOKEN or GITLAB_TOKEN: Token for GitLab API access
#
# This script checks that the pipeline for the target SHA has completed successfully.
# It fails fast if pipeline is missing, failed, or in progress.
#
# Future: Can be wrapped in a retry loop for polling behavior.

TARGET_SHA="${TARGET_SHA:?TARGET_SHA not set}"
GITLAB_PROJECT_ID="${GITLAB_PROJECT_ID:?GITLAB_PROJECT_ID not set}"
GITLAB_REPO="${GITLAB_REPO:?GITLAB_REPO not set}"

# Determine GitLab API URL and token
GITLAB_SERVER_URL="${GITLAB_SERVER_URL:-https://gitlab.ddbuild.io}"
GITLAB_API_V4_URL="${GITLAB_API_V4_URL:-$GITLAB_SERVER_URL/api/v4}"

# Use GITLAB_TOKEN if provided, otherwise use CI_JOB_TOKEN
GITLAB_TOKEN="${GITLAB_TOKEN:-${CI_JOB_TOKEN:-}}"
if [ -z "$GITLAB_TOKEN" ]; then
  echo "❌ ERROR: Neither GITLAB_TOKEN nor CI_JOB_TOKEN is set"
  exit 1
fi

echo "Verifying GitLab Pipeline for $GITLAB_REPO (ID: $GITLAB_PROJECT_ID) @ $TARGET_SHA"
echo ""

# Get the most recent pipeline for this commit
# GitLab API: GET /projects/:id/pipelines?sha=:sha&order_by=id&sort=desc
echo "Fetching pipeline from GitLab API..."
pipelines_response=$(curl -s \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_API_V4_URL/projects/$GITLAB_PROJECT_ID/pipelines?sha=$TARGET_SHA&order_by=id&sort=desc&per_page=20" \
  2>/dev/null || echo "[]")

# Check if response is empty
if [ -z "$pipelines_response" ]; then
  echo "❌ FAILED: No response from GitLab API"
  exit 1
fi

# Check if response is an error object (has 'message' field)
if echo "$pipelines_response" | jq -e '.message' &>/dev/null; then
  error_msg=$(echo "$pipelines_response" | jq -r '.message // "Unknown error"')
  echo "❌ FAILED: GitLab API error: $error_msg"
  exit 1
fi

# Ensure it's an array and get the first element
# If it's not an array, treat as error
if ! echo "$pipelines_response" | jq -e 'type == "array"' &>/dev/null; then
  echo "❌ FAILED: Unexpected GitLab API response format"
  echo "Response: $(echo "$pipelines_response" | head -c 200)"
  exit 1
fi

# Check if array is empty
if [ "$(echo "$pipelines_response" | jq 'length')" -eq 0 ]; then
  echo "❌ FAILED: No pipelines found for commit $TARGET_SHA"
  exit 1
fi

# Get the most recent pipeline (first in the list, sorted by id desc)
pipeline=$(echo "$pipelines_response" | jq -r '.[0]')

if [ -z "$pipeline" ] || [ "$pipeline" = "null" ]; then
  echo "❌ FAILED: No pipelines found for commit $TARGET_SHA"
  exit 1
fi

pipeline_id=$(echo "$pipeline" | jq -r '.id // "unknown"')
pipeline_status=$(echo "$pipeline" | jq -r '.status // "unknown"')
pipeline_ref=$(echo "$pipeline" | jq -r '.ref // "unknown"')
pipeline_created=$(echo "$pipeline" | jq -r '.created_at // "unknown"')
pipeline_web_url=$(echo "$pipeline" | jq -r '.web_url // empty')

# If web_url not available, construct it
if [ -z "$pipeline_web_url" ]; then
  pipeline_web_url="$GITLAB_SERVER_URL/$GITLAB_REPO/-/pipelines/$pipeline_id"
fi

echo "Pipeline ID: $pipeline_id"
echo "Ref: $pipeline_ref"
echo "Status: $pipeline_status"
echo "Created: $pipeline_created"
echo "URL: $pipeline_web_url"
echo ""

# Function to get failing jobs
get_failing_jobs() {
  local jobs=$(timeout 10 curl -s \
    --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
    "$GITLAB_API_V4_URL/projects/$GITLAB_PROJECT_ID/pipelines/$pipeline_id/jobs" \
    2>&1)

  if [ $? -eq 0 ]; then
    echo "$jobs" | jq -r '.[] | select(.status == "failed") | "  - \(.name) (stage: \(.stage))"' 2>/dev/null
  fi
}

# Check pipeline status - ONLY success is acceptable
if [ "$pipeline_status" = "success" ]; then
  echo "✅ GitLab pipeline completed successfully!"
  exit 0
fi

# Pipeline is not in success state - fail with details
echo "❌ FAILED: Pipeline did not complete successfully"
echo "  Status: $pipeline_status"
echo ""

# Get more details if it's a failed pipeline
if [ "$pipeline_status" = "failed" ]; then
  echo "Failing jobs:"
  failing_jobs=$(get_failing_jobs)
  if [ -n "$failing_jobs" ]; then
    echo "$failing_jobs"
  else
    echo "  (unable to retrieve failing jobs)"
  fi
  echo ""
fi

echo "View pipeline: $pipeline_web_url"
exit 1
