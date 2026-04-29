#!/usr/bin/env bash
set -euo pipefail

# Usage: generate-metadata-txt.sh
# Outputs key=value build metadata to stdout.
# Reads GitLab CI environment variables (all optional):
#   CI_COMMIT_SHA, CI_COMMIT_SHORT_SHA, CI_COMMIT_REF_NAME,
#   CI_PIPELINE_ID, PACKAGE_VERSION

COMMIT_SHA="${CI_COMMIT_SHA:-unknown}"
COMMIT_SHORT="${CI_COMMIT_SHORT_SHA:-${COMMIT_SHA:0:8}}"
REF_NAME="${CI_COMMIT_REF_NAME:-unknown}"
PIPELINE_ID="${CI_PIPELINE_ID:-unknown}"
PKG_VERSION="${PACKAGE_VERSION:-unknown}"
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat << EOF
commit_sha=${COMMIT_SHA}
commit_short_sha=${COMMIT_SHORT}
ref_name=${REF_NAME}
pipeline_id=${PIPELINE_ID}
package_version=${PKG_VERSION}
created_at=${CREATED_AT}
EOF
