#!/bin/bash
set -euo pipefail

# Download release artifacts from GitLab pipeline
#
# Expected environment variables:
#   TARGET_SHA: Git SHA to download artifacts for
#   GITLAB_PROJECT_ID: GitLab project ID (numeric)
#   GITLAB_API_V4_URL: GitLab API URL (defaults to GITLAB_SERVER_URL/api/v4)
#   CI_JOB_TOKEN or GITLAB_TOKEN: Token for GitLab API access
#
# This script:
# 1. Finds the pipeline for the target SHA
# 2. Downloads artifacts from the build job (download_ddtrace_artifacts)
# 3. Falls back to S3 if GitLab artifacts are unavailable

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

# S3 bucket configuration
S3_BUCKET="${S3_BUCKET:-dd-trace-py-builds}"
S3_REGION="${S3_REGION:-us-east-1}"

# Create output directory
ARTIFACT_DIR="release-artifacts/wheels"
mkdir -p "$ARTIFACT_DIR"

echo "Downloading release artifacts for $GITLAB_REPO (ID: $GITLAB_PROJECT_ID) @ $TARGET_SHA"
echo ""

# Get the pipeline for this commit
echo "Fetching pipeline for commit $TARGET_SHA..."
pipeline=$(curl -s \
  --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  "$GITLAB_API_V4_URL/projects/$GITLAB_PROJECT_ID/pipelines?sha=$TARGET_SHA&order_by=id&sort=desc&per_page=1" \
  2>/dev/null || echo "[]")

pipeline=$(echo "$pipeline" | jq -r '.[0] // empty')

if [ -z "$pipeline" ] || [ "$pipeline" = "null" ]; then
  echo "❌ FAILED: No pipeline found for commit $TARGET_SHA"
  exit 1
fi

pipeline_id=$(echo "$pipeline" | jq -r '.id // empty')
pipeline_status=$(echo "$pipeline" | jq -r '.status // "unknown"')

if [ -z "$pipeline_id" ]; then
  echo "❌ FAILED: Could not extract pipeline ID from response"
  exit 1
fi

echo "Pipeline ID: $pipeline_id"
echo "Pipeline status: $pipeline_status"
echo ""

# Try to download from GitLab artifacts
download_from_gitlab() {
  echo "Attempting to download artifacts from GitLab pipeline..."

  # Get the job that produces wheel/sdist artifacts
  # Looking for the download_ddtrace_artifacts job
  jobs_response=$(curl -s \
    --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
    "$GITLAB_API_V4_URL/projects/$GITLAB_PROJECT_ID/pipelines/$pipeline_id/jobs?per_page=100" \
    2>/dev/null || echo "[]")

  # Find jobs with artifacts (looking for wheel/sdist producing jobs)
  artifact_jobs=$(echo "$jobs_response" | jq -r '.[] | select(.artifacts_file != null) | .name' 2>/dev/null || echo "")

  if [ -z "$artifact_jobs" ]; then
    echo "⚠️  No jobs with artifacts found in pipeline $pipeline_id"
    return 1
  fi

  echo "Found artifact jobs:"
  echo "$artifact_jobs"
  echo ""

  # Try to download from each job with artifacts
  # Prioritize download_ddtrace_artifacts
  while IFS= read -r job_name; do
    echo "Trying to download from job: $job_name"

    # Get the job ID
    job_id=$(echo "$jobs_response" | jq -r ".[] | select(.name == \"$job_name\") | .id" | head -1)

    if [ -z "$job_id" ]; then
      echo "  ⚠️  Could not find job ID for $job_name"
      continue
    fi

    # Download artifacts from this job
    artifacts_url="$GITLAB_API_V4_URL/projects/$GITLAB_PROJECT_ID/jobs/$job_id/artifacts"
    if curl -s \
      --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
      --output artifacts.zip \
      "$artifacts_url" 2>/dev/null; then

      if [ -f artifacts.zip ]; then
        echo "  ✓ Downloaded artifacts from $job_name"
        unzip -q artifacts.zip -d "$ARTIFACT_DIR"
        rm artifacts.zip
        return 0
      fi
    fi

    echo "  ✗ Failed to download from $job_name"
  done <<< "$artifact_jobs"

  return 1
}

# Fallback: download from S3
download_from_s3() {
  echo "Falling back to S3 bucket download..."

  if ! command -v aws &> /dev/null; then
    echo "❌ AWS CLI not available for S3 fallback"
    return 1
  fi

  echo "Downloading wheels from s3://$S3_BUCKET/$TARGET_SHA/"

  # Set up AWS credentials if needed
  if [ -z "${AWS_ACCESS_KEY_ID:-}" ] && [ -z "${AWS_EC2_METADATA_SERVICE_PUT_RELATIVE_TOKEN_URI:-}" ]; then
    echo "⚠️  AWS credentials not configured, trying anonymous S3 access..."
  fi

  # Download wheels and sdist
  if aws s3 cp \
    "s3://$S3_BUCKET/$TARGET_SHA/" \
    "$ARTIFACT_DIR/" \
    --region "$S3_REGION" \
    --recursive \
    --exclude "*" \
    --include "*.whl" \
    --include "*.tar.gz" \
    2>/dev/null; then
    echo "✓ Successfully downloaded from S3"
    return 0
  fi

  return 1
}

# Try GitLab first, then S3
if download_from_gitlab; then
  echo ""
  echo "✅ Successfully downloaded artifacts from GitLab"
elif download_from_s3; then
  echo ""
  echo "✅ Successfully downloaded artifacts from S3 (fallback)"
else
  echo ""
  echo "❌ FAILED: Could not download artifacts from GitLab or S3"
  exit 1
fi

# List what we downloaded
echo ""
echo "Downloaded artifacts:"
find "$ARTIFACT_DIR" -type f | sort | sed 's/^/  /'

# Verify we got something
artifact_count=$(find "$ARTIFACT_DIR" -type f | wc -l)
if [ "$artifact_count" -eq 0 ]; then
  echo "❌ FAILED: No artifacts were downloaded"
  exit 1
fi

echo ""
echo "✅ Downloaded $artifact_count artifact(s)"
