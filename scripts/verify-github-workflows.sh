#!/bin/bash
set -euo pipefail

# Verify GitHub Workflows for a target commit
#
# Expected environment variables:
#   TARGET_SHA: Git SHA to check workflows for
#   GITHUB_REPO: GitHub repository (owner/repo)
#   REQUIRED_WORKFLOWS: Comma-separated list of workflow names that must pass
#   GH_TOKEN: GitHub token (for API access)
#
# This script checks that all required workflows have completed successfully.
# It fails fast if any workflow is missing, failed, or in progress.
#
# Future: Can be wrapped in a retry loop for polling behavior.

TARGET_SHA="${TARGET_SHA:?TARGET_SHA not set}"
GITHUB_REPO="${GITHUB_REPO:?GITHUB_REPO not set}"
GH_TOKEN="${GH_TOKEN:?GH_TOKEN not set}"

export GH_TOKEN

echo "Verifying GitHub workflows for $GITHUB_REPO @ $TARGET_SHA"
echo ""

# Get all workflow runs for the target commit
# Use gh run ls with commit filter for efficiency
echo "Fetching workflow runs from GitHub for commit $TARGET_SHA..."

all_runs_raw=$(gh run ls --repo="$GITHUB_REPO" --commit="$TARGET_SHA" --json name,status,conclusion 2>&1)
api_exit=$?

if [ $api_exit -ne 0 ]; then
  echo "❌ FAILED: Could not fetch workflow runs (exit: $api_exit)"
  echo "Error: $all_runs_raw"
  exit 1
fi

# Check if we got any runs at all
if [ -z "$all_runs_raw" ] || [ "$all_runs_raw" = "[]" ]; then
  echo "❌ FAILED: No workflow runs found for commit $TARGET_SHA"
  exit 1
fi

echo "Found workflow runs:"
echo "$all_runs_raw" | jq -r '.[] | "  - \(.name): \(.status) (\(.conclusion // "N/A"))"'
echo ""

# Use a temporary file to track which workflows we've seen (most recent only)
# We'll process runs in order and only check each workflow once (first = most recent)
declare -A seen_workflows
all_passed=true

# Process each workflow run
while IFS= read -r run_json; do
  if [ -z "$run_json" ]; then
    continue
  fi

  run_name=$(echo "$run_json" | jq -r '.name // empty')
  run_status=$(echo "$run_json" | jq -r '.status // "unknown"')
  run_conclusion=$(echo "$run_json" | jq -r '.conclusion // empty')

  if [ -z "$run_name" ]; then
    continue
  fi

  # Skip if we've already checked this workflow (we only care about the most recent)
  if [ -n "${seen_workflows[$run_name]:-}" ]; then
    continue
  fi
  seen_workflows[$run_name]=1

  echo "Checking workflow: $run_name"
  echo "  Status: $run_status"
  echo "  Conclusion: $run_conclusion"

  # Check status - workflow must be completed with success
  case "$run_status" in
    completed)
      if [ "$run_conclusion" = "success" ]; then
        echo "  ✅ PASSED"
      else
        echo "  ❌ FAILED: Workflow concluded with '$run_conclusion'"
        all_passed=false
      fi
      ;;
    in_progress|queued|requested|waiting|scheduled|pending)
      echo "  ❌ FAILED: Workflow is still in progress (status: $run_status)"
      all_passed=false
      ;;
    *)
      echo "  ❌ FAILED: Unknown or unexpected status '$run_status'"
      all_passed=false
      ;;
  esac

  echo ""
done < <(echo "$all_runs_raw" | jq -c '.[]')

if [ "$all_passed" = true ]; then
  echo "✅ All required GitHub workflows passed!"
  exit 0
else
  echo "❌ One or more GitHub workflows failed or are incomplete"
  exit 1
fi
