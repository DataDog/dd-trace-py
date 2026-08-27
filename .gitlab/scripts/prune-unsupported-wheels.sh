#!/usr/bin/env bash
# Deletes wheels whose Python ABI tag is built for CI signal but is not fit to publish.
#
# Usage: prune-unsupported-wheels.sh <dir> [<dir> ...]
#
# Every job that hands a ddtrace wheel to something outside this pipeline must call this
# first. Today that is:
#   * .gitlab/scripts/upload-wheels-to-s3.sh   -> the public dd-trace-py-builds bucket
#   * .gitlab/release.yml (.release_pypi)      -> PyPI
#   * .gitlab/package.yml ("ddtrace package")  -> the artifact release_pypi_prod and
#                                                 "upload all" consume
#   * .gitlab/package.yml (.patch_wheel_versions_base) -> the pypi-private-prereleases index
#
# tests/internal/test_unsupported_wheel_pruning.py asserts that every file under .gitlab/
# that runs a publish command also calls this script, so a new upload path cannot skip it
# without failing CI.
#
# Debug symbol archives (debugwheelhouse/*.zip) are deliberately not pruned: they are not
# installable and carry no ABI risk. Pass only wheel directories.

set -euo pipefail

# Python ABI tags withheld from every publication channel. Single source of truth.
#
# TODO(py-315): cp315 wheels are built by "build linux" and "build linux serverless" under
# allow_failure so that 3.15 keeps producing CI signal, but they are compiled against the
# 3.15.0b1 PyThreadState layout and are ABI-broken. Drop cp315 from this list once #19861
# makes those wheels correct and 3.15 is a supported target.
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
