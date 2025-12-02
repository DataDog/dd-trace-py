#!/usr/bin/env bash
set -e -o pipefail

# If we have a tag (e.g. v2.21.1), then use the PyPI published wheel
# Otherwise, try to download from S3 by commit SHA, or build the wheel from scratch
if [[ -n "${BASELINE_TAG}" ]];
then
  python3.9 -m pip download --no-deps "ddtrace==${BASELINE_TAG:1}"
else
  # Try to download the wheel from S3 using the baseline commit SHA
  S3_BUCKET="dd-trace-py-builds"
  S3_INDEX_URL="https://${S3_BUCKET}.s3.amazonaws.com/${BASELINE_COMMIT_SHA}/index.html"

  echo "Attempting to download wheel from S3 index: ${S3_INDEX_URL}"
  if python3.9 -m pip download --no-deps --index-url "${S3_INDEX_URL}" ddtrace 2>/dev/null; then
    echo "Successfully downloaded wheel from S3"
  else
    echo "Failed to download from S3, building wheel from scratch..."
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
  fi
fi
