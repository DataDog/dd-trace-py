#!/usr/bin/env bash
# Download a build artifact from the latest successful run of a job on a
# given branch.  Designed for use in CI jobs that need to compare PR changes
# against the base branch without recompiling.
#
# TODO: Ideally we would resolve the merge-base SHA and find the pipeline
# that ran on that specific commit, but the GitLab pipelines API is not
# accessible with CI_JOB_TOKEN.  Since CI runs on every commit to main the
# latest artifact is a close approximation.
#
# Usage:
#   fetch_base_artifact.sh [options] -o <output-dir>
#
# Options:
#   -a ARCH        Architecture tag         (default: amd64)
#   -p PYTHON_TAG  Python build tag         (default: cp312-cp312)
#   -i IMAGE_TAG   Manylinux image tag      (default: v85383392-751efc0-manylinux2014_x86_64)
#   -j JOB_NAME    Base job name template   (default: "build linux")
#   -r REF         Branch to fetch from     (default: main)
#   -o OUTPUT_DIR  Where to extract artifacts (required)
#
# Required environment variables (set automatically in GitLab CI):
#   CI_JOB_TOKEN, CI_API_V4_URL, CI_PROJECT_ID
#
# Exit codes:
#   0  Success
#   1  Missing arguments / environment
#   2  Failed to download artifacts

set -euo pipefail

# --- defaults ----------------------------------------------------------------
ARCH="amd64"
PYTHON_TAG="cp312-cp312"
IMAGE_TAG="v85383392-751efc0-manylinux2014_x86_64"
JOB_NAME="build linux"
REF="main"
OUTPUT_DIR=""

usage() {
    sed -n '2,/^$/{ s/^# \?//; p }' "$0"
    exit 1
}

while getopts "a:p:i:j:r:o:h" opt; do
    case "$opt" in
        a) ARCH="$OPTARG" ;;
        p) PYTHON_TAG="$OPTARG" ;;
        i) IMAGE_TAG="$OPTARG" ;;
        j) JOB_NAME="$OPTARG" ;;
        r) REF="$OPTARG" ;;
        o) OUTPUT_DIR="$OPTARG" ;;
        h|*) usage ;;
    esac
done

if [ -z "$OUTPUT_DIR" ]; then
    echo "ERROR: -o <output-dir> is required" >&2
    usage
fi

for var in CI_JOB_TOKEN CI_API_V4_URL CI_PROJECT_ID; do
    if [ -z "${!var:-}" ]; then
        echo "ERROR: $var is not set.  This script must run inside GitLab CI." >&2
        exit 1
    fi
done

FULL_JOB_NAME="${JOB_NAME}: [${ARCH}, ${PYTHON_TAG}, ${IMAGE_TAG}]"
ENCODED_JOB_NAME=$(python -c "import urllib.parse; print(urllib.parse.quote('${FULL_JOB_NAME}'))")

# --- download artifacts -------------------------------------------------------
ARTIFACT_URL="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/jobs/artifacts/${REF}/download?job=${ENCODED_JOB_NAME}"

echo "==> Downloading artifacts for '${FULL_JOB_NAME}' from ref '${REF}'..."
echo "    URL: ${ARTIFACT_URL}"

TMPFILE=$(mktemp "${TMPDIR:-/tmp}/base-artifacts-XXXXXX.zip")
trap 'rm -f "$TMPFILE"' EXIT

HTTP_CODE=$(curl --silent --show-error --location \
    --output "$TMPFILE" --write-out "%{http_code}" \
    --header "JOB-TOKEN: $CI_JOB_TOKEN" \
    "$ARTIFACT_URL")

if [ "$HTTP_CODE" -ne 200 ]; then
    echo "ERROR: Artifact download failed with HTTP ${HTTP_CODE}." >&2
    echo "       URL: ${ARTIFACT_URL}" >&2
    echo "       Response body:" >&2
    cat "$TMPFILE" >&2
    echo >&2
    exit 2
fi

mkdir -p "$OUTPUT_DIR"
python -c "
import zipfile, sys
with zipfile.ZipFile(sys.argv[1]) as zf:
    zf.extractall(sys.argv[2])
" "$TMPFILE" "$OUTPUT_DIR"
echo "==> Artifacts extracted to ${OUTPUT_DIR}"
