#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
suite_slug=$(printf '%s' "${SUITE_NAME}" | tr '[:upper:]_' '[:lower:]-' | sed -E 's/[^a-z0-9]+/-/g; s/^-//; s/-$//')
environments=( $(scripts/test-env list "${SUITE_NAME}") )
# Get the sha256sum of all the requirements files combined.
for environment in "${environments[@]}"; do
  req_file="./.uv/${suite_slug}--${environment}.txt"
  if [ -f "${req_file}" ]; then
    cat "${req_file}"
  fi
done | sort | sha256sum | awk '{print $1}'
