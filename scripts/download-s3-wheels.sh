#!/usr/bin/env bash
# Downloads wheels from S3 for a given commit SHA, polling until available.
#
# Usage: download-s3-wheels.sh <commit_sha> <output_dir> [suffix]
# Examples:
#   download-s3-wheels.sh abc123 ./wheels manylinux2014_x86_64
#   download-s3-wheels.sh abc123 ./wheels

set -euo pipefail

COMMIT_SHA="${1:-}"
OUTPUT_DIR="${2:-}"
SUFFIX="${3:-}"

if [ -z "$COMMIT_SHA" ] || [ -z "$OUTPUT_DIR" ]; then
  echo "Usage: $0 <commit_sha> <output_dir> [suffix]" >&2
  exit 1
fi

BUCKET="dd-trace-py-builds"
BASE_URL="https://${BUCKET}.s3.amazonaws.com/${COMMIT_SHA}"

if [ -n "$SUFFIX" ]; then
  INDEX_FILE="index-${SUFFIX}.html"
else
  INDEX_FILE="index.html"
fi

INDEX_URL="${BASE_URL}/${INDEX_FILE}"

POLL_TIMEOUT="${POLL_TIMEOUT:-1800}"  # 30 minutes default
POLL_INTERVAL="${POLL_INTERVAL:-30}"  # 30 seconds default

echo "Polling for: ${INDEX_URL}"
echo "Timeout: ${POLL_TIMEOUT}s, Interval: ${POLL_INTERVAL}s"

elapsed=0
while true; do
  if curl -sf -o /dev/null "${INDEX_URL}"; then
    echo "Index found after ${elapsed}s"
    break
  fi

  if [ "$elapsed" -ge "$POLL_TIMEOUT" ]; then
    echo "ERROR: Timed out after ${POLL_TIMEOUT}s waiting for ${INDEX_URL}" >&2
    exit 1
  fi

  echo "Not available yet (${elapsed}s elapsed), retrying in ${POLL_INTERVAL}s..."
  sleep "${POLL_INTERVAL}"
  elapsed=$((elapsed + POLL_INTERVAL))
done

mkdir -p "${OUTPUT_DIR}"

echo "Downloading wheels to ${OUTPUT_DIR}..."
wget -r -l1 -np -nd -A "*.whl" -P "${OUTPUT_DIR}" "${INDEX_URL}"

echo "Download complete. Contents of ${OUTPUT_DIR}:"
ls -la "${OUTPUT_DIR}"
