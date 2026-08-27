#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
lock_files=( $(scripts/test-env list --paths "${SUITE_NAME}") )
# Get the sha256sum of all the uv locks combined.
for lock_file in "${lock_files[@]}"; do
  cat "${lock_file}"
done | sort | sha256sum | awk '{print $1}'
