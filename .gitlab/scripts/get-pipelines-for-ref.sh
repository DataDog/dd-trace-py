#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./get-pipelines-for-ref.sh <ref>
#
# Example:
#   ./get-pipelines-for-ref.sh 3dbf82a
#   ./get-pipelines-for-ref.sh v2.1.0
#
# Environment:
#   GITLAB_TOKEN (required)

GITLAB_API_URL=${GITLAB_API_URL:"https://gitlab.ddbuild.io/api/v4"}
GITLAB_PROJECT_ID=${GITLAB_PROJECT_ID:"358"}

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <commit-sha-or-tag>" >&2
  exit 1
fi

if [[ -z "${GITLAB_TOKEN:-}" ]]; then
  echo "GITLAB_TOKEN is not set" >&2
  exit 1
fi

REF="$1"

curl --fail --silent --show-error \
  --header "PRIVATE-TOKEN: ${GITLAB_TOKEN}" \
  "${GITLAB_API_URL}/projects/${GITLAB_PROJECT_ID}/pipelines?ref=${REF}"
