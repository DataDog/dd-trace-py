#!/usr/bin/env bash
# Build macOS wheels for each Python version listed in $PYTHON_VERSIONS
# (space-separated, e.g. "3.9 3.10 3.11"). Shared between the
# "build macos amd64" and "build macos arm64" GitLab jobs.
set -euo pipefail

if ! command -v sccache &> /dev/null; then
  brew install sccache
fi

for python_version in ${PYTHON_VERSIONS}; do
  echo -e "\e[0Ksection_start:`date +%s`:build_macos_${python_version}\r\e[0KBuilding macOS wheel for Python ${python_version}"

  export UV_PYTHON="${python_version}"
  .gitlab/scripts/build-wheel.sh

  echo -e "\e[0Ksection_end:`date +%s`:build_macos_${python_version}\r\e[0K"
done

sccache --show-stats
