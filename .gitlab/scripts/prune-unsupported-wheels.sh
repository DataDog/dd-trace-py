#!/usr/bin/env bash
# Prune unsupported tags from PyPI/adms; S3 SHA index keeps them for gate installs.
# Usage: prune-unsupported-wheels.sh <dir> [<dir> ...]

set -euo pipefail

UNSUPPORTED_TAGS=("cp315")

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <dir> [<dir> ...]" >&2
  exit 1
fi

shopt -s nullglob

for dir in "$@"; do
  if [ ! -d "${dir}" ]; then
    echo "[ERROR] ${dir} is not a directory -- refusing to publish unpruned wheels" >&2
    exit 1
  fi
  for tag in "${UNSUPPORTED_TAGS[@]}"; do
    matches=("${dir}"/*"${tag}"*.whl)
    if [ ${#matches[@]} -eq 0 ]; then
      continue
    fi
    printf 'Pruning %s unsupported %s wheel(s) from %s/:\n' "${#matches[@]}" "${tag}" "${dir}"
    printf '  %s\n' "${matches[@]}"
    rm -f -- "${matches[@]}"
  done
done
