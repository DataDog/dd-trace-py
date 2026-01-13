#!/usr/bin/env bash
# Uploads wheels from pywheels/ to S3 and creates index + helper files
#
# Usage: upload-wheels-to-s3.sh <s3_path_prefix> [suffix]
# Examples:
#   upload-wheels-to-s3.sh ${CI_COMMIT_SHA}                       → index.html, download.sh, install.sh
#   upload-wheels-to-s3.sh ${CI_PIPELINE_ID} manylinux2014_x86_64 → index-manylinux2014_x86_64.html, etc.

set -euo pipefail

BUCKET="${BUCKET:-dd-trace-py-builds}"
S3_PATH="${1:-}"
SUFFIX="${2:-}"

if [ -z "$S3_PATH" ]; then
  echo "Usage: $0 <s3_path_prefix> [suffix]" >&2
  exit 1
fi

shopt -s nullglob
WHEELS=(pywheels/*.whl pywheels/*.tar.gz)

if [ ${#WHEELS[@]} -eq 0 ]; then
  echo "No packages found in pywheels/"
  exit 0
fi

# Determine filenames based on suffix
if [ -n "$SUFFIX" ]; then
  INDEX_FILE="index-${SUFFIX}.html"
  DOWNLOAD_FILE="download-${SUFFIX}.sh"
  INSTALL_FILE="install-${SUFFIX}.sh"
else
  INDEX_FILE="index.html"
  DOWNLOAD_FILE="download.sh"
  INSTALL_FILE="install.sh"
fi

echo "Uploading ${#WHEELS[@]} package(s) to s3://${BUCKET}/${S3_PATH}/"

# Upload wheel files
for wheel in "${WHEELS[@]}"; do
  aws s3 cp "$wheel" "s3://${BUCKET}/${S3_PATH}/$(basename "$wheel")"
done

# Generate and upload index + helper scripts
S3_BASE_URL="https://${BUCKET}.s3.amazonaws.com/${S3_PATH}"
.gitlab/scripts/generate-index-html.sh | aws s3 cp - "s3://${BUCKET}/${S3_PATH}/${INDEX_FILE}" --content-type text/html
.gitlab/scripts/generate-download-script.sh "${S3_BASE_URL}" | aws s3 cp - "s3://${BUCKET}/${S3_PATH}/${DOWNLOAD_FILE}" --content-type text/x-shellscript
.gitlab/scripts/generate-install-script.sh "${S3_BASE_URL}" | aws s3 cp - "s3://${BUCKET}/${S3_PATH}/${INSTALL_FILE}" --content-type text/x-shellscript

echo "Uploaded to ${S3_PATH}/:"
echo "  Index:    ${S3_BASE_URL}/${INDEX_FILE}"
echo "  Download: ${S3_BASE_URL}/${DOWNLOAD_FILE}"
echo "  Install:  ${S3_BASE_URL}/${INSTALL_FILE}"
