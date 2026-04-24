#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
if [[ -n "${RIOT_HASHES:-}" ]]; then
  read -r -a hashes <<< "${RIOT_HASHES}"
else
  hashes=( $(./.gitlab/scripts/get-riot-hashes.sh "${SUITE_NAME}") )
fi
# Get the sha256sum of all the requirements files combined
for hash in "${hashes[@]}"; do
  req_file="./.riot/requirements/${hash}.txt"
  if [ -f "${req_file}" ]; then
    cat "${req_file}"
  fi
done | sort | sha256sum | awk '{print $1}'
