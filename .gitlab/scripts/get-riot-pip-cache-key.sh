#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
hashes=( $(./.gitlab/scripts/get-riot-hashes.sh "${SUITE_NAME}") )
# Get the sha256sum of all the requirements files combined
for hash in "${hashes[@]}"; do
  req_file="./.riot/requirements/${hash}.txt"
  if [ -f "${req_file}" ]; then
    cat "${req_file}"
  fi
done | sort | sha256sum | awk '{print $1}'
