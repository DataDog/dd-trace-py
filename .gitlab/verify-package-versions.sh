#!/usr/bin/env bash
set -euo pipefail

WHEEL_FILES=(pywheels/*.whl)
if [ ${#WHEEL_FILES[@]} -eq 0 ]; then
  echo "No wheels found in pywheels/"; exit 1
fi

VERSION_TAG="${CI_COMMIT_TAG#v}"
echo "Verifying package version ${VERSION_TAG}"

for wf in "${WHEEL_FILES[@]}"; do
  echo "Checking wheel file: ${wf}"
  WHEEL_VERSION=$(basename "${wf}" | awk -F '-' '{print $2}')
  if [ "${WHEEL_VERSION}" != "${VERSION_TAG}" ]; then
    echo "ERROR: Wheel version ${WHEEL_VERSION} does not match tag version ${VERSION_TAG}"
    exit 1
  fi
done

SDIST_FILES=(pywheels/*.tar.gz)
if [ ${#SDIST_FILES[@]} -eq 0 ]; then
  echo "No sdist files found in pywheels/"; exit 1
fi
for sf in "${SDIST_FILES[@]}"; do
  echo "Checking sdist file: ${sf}"
  SDIST_VERSION=$(basename "${sf}" | awk -F '-' '{print $2}' | sed 's/\.tar\.gz$//')
  if [ "${SDIST_VERSION}" != "${VERSION_TAG}" ]; then
      echo "ERROR: Sdist version ${SDIST_VERSION} does not match tag version ${VERSION_TAG}"
      exit 1
  fi
done
