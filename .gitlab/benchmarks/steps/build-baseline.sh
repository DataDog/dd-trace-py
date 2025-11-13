#!/usr/bin/env bash
set -e -o pipefail

# If we have a tag (e.g. v2.21.1), then use the PyPI published wheel
if [[ -n "${BASELINE_TAG}" ]];
then
  python3.9 -m pip download --no-deps "ddtrace==${BASELINE_TAG:1}"
  exit 0
fi


# If we have a commit SHA, try to download the prebuild wheel from GitHub Actions
if [[ -n "${BASELINE_COMMIT_SHA}" ]];
then
  # Try to download the wheel from the public S3 bucket if we have one
  .gitlab/scripts/get-pipelines-for-ref.sh "${BASELINE_COMMIT_SHA}" || true

  exit 1
  # CI_COMMIT_SHA="${BASELINE_COMMIT_SHA}" .gitlab/download-wheels-from-gh-actions.sh || true

  # If we have a ./pywheels/ for cp39 manylinux x86_64 use that wheel
  # if [ -d "./pywheels" ] && ls ./pywheels/ddtrace-*cp39*manylinux* | grep -q "x86_64";
  # then
  #   echo "Using prebuilt wheel from GitHub Actions for baseline commit ${BASELINE_COMMIT_SHA}"
  #   cp ./pywheels/ddtrace-*cp39*manylinux*x86_64.whl ./
  #   exit 0
  # fi
fi

# Otherwise, build from source
ulimit -c unlimited
curl -sSf https://sh.rustup.rs | sh -s -- -y;
export PATH="$HOME/.cargo/bin:$PATH"
echo "Building wheel for ${BASELINE_BRANCH}:${BASELINE_COMMIT_SHA}"
git checkout "${BASELINE_COMMIT_SHA}"
mkdir ./tmp
PYO3_PYTHON=python3.9 CIBW_BUILD=1 python3.9 -m pip wheel --no-deps -w ./tmp/ ./
for wheel in ./tmp/*.whl;
do
  auditwheel repair "$wheel" --plat "manylinux2014_x86_64" -w ./
done
