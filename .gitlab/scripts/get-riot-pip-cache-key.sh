#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
# Cache identity covers the full semantic suite and must not depend on one
# physical allocation job's node index or runtime test slice.
hashes=( $(riot list --hash-only "${SUITE_NAME}" | sort -u) )
# Get the sha256sum of all the requirements files combined
for hash in "${hashes[@]}"; do
  req_file="./.riot/requirements/${hash}.txt"
  if [ -f "${req_file}" ]; then
    cat "${req_file}"
  fi
done | sort | sha256sum | awk '{print $1}'
