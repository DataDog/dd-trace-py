#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
hashes=( $(./.gitlab/scripts/get-riot-hashes.sh "${SUITE_NAME}") )
# Get the sha256sum of all the uv locks combined.
for hash in "${hashes[@]}"; do
  lock_files=(.uv/*--"${hash}".txt)
  for lock_file in "${lock_files[@]}"; do
    if [ -f "${lock_file}" ]; then
      cat "${lock_file}"
    fi
  done
done | sort | sha256sum | awk '{print $1}'
