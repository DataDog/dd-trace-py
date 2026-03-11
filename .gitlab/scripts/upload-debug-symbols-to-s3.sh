#!/usr/bin/env bash
# Uploads debug symbol packages from debugwheelhouse/ to S3
#
# Usage: upload-debug-symbols-to-s3.sh <s3_path_prefix>
# Examples:
#   upload-debug-symbols-to-s3.sh ${CI_COMMIT_SHA}
#   upload-debug-symbols-to-s3.sh ${CI_PIPELINE_ID}

set -euo pipefail

BUCKET="${BUCKET:-dd-trace-py-builds}"
S3_PATH="${1:-}"

if [ -z "$S3_PATH" ]; then
  echo "Usage: $0 <s3_path_prefix>" >&2
  exit 1
fi

shopt -s nullglob
SYMBOLS=(debugwheelhouse/*.zip)

if [ ${#SYMBOLS[@]} -eq 0 ]; then
  echo "No debug symbol packages found in debugwheelhouse/"
  exit 0
fi

echo "Uploading ${#SYMBOLS[@]} debug symbol package(s) to s3://${BUCKET}/${S3_PATH}/debug-symbols/"

for symbol_pkg in "${SYMBOLS[@]}"; do
  aws s3 cp "$symbol_pkg" "s3://${BUCKET}/${S3_PATH}/debug-symbols/$(basename "$symbol_pkg")"
done

echo "Uploaded debug symbols to s3://${BUCKET}/${S3_PATH}/debug-symbols/"
