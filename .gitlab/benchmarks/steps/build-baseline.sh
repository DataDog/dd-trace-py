#!/usr/bin/env bash
set -e -o pipefail

# If we have a tag (e.g. v2.21.1), then use the PyPI published wheel
# Otherwise, try to download from S3 by commit SHA, or build the wheel from scratch
if [[ -n "${BASELINE_TAG}" ]];
then
  python3.14 -m pip download --no-deps "ddtrace==${BASELINE_TAG:1}"
else
  # Try to download the wheel from S3 using the baseline commit SHA
  S3_BUCKET="dd-trace-py-builds"
  S3_INDEX_URL="https://${S3_BUCKET}.s3.amazonaws.com/${BASELINE_COMMIT_SHA}/index.html"

  echo "Attempting to download wheel from S3 index: ${S3_INDEX_URL}"
  if python3.14 -m pip download --no-index --no-deps --find-links "${S3_INDEX_URL}" --pre ddtrace 2>/dev/null; then
    echo "Successfully downloaded wheel from S3"
  else
    echo "Failed to download from S3, building wheel from scratch..."
    ulimit -c unlimited
    # Skip rustup install if Rust is already available (e.g. when using the
    # dd-trace-py build image as PACKAGE_IMAGE, which ships Rust pre-installed).
    if ! command -v cargo &>/dev/null; then
      for i in 1 2 3; do
        curl -sSf https://sh.rustup.rs | sh -s -- -y && break
        echo "rustup install attempt $i failed, retrying..."
        sleep 5
        [ "$i" -eq 3 ] && { echo "Failed to install rustup after 3 attempts"; exit 1; }
      done
      export PATH="$HOME/.cargo/bin:$PATH"
    else
      echo "Rust toolchain already available, skipping rustup install"
    fi
    echo "Building wheel for ${BASELINE_BRANCH}:${BASELINE_COMMIT_SHA}"
    git checkout "${BASELINE_COMMIT_SHA}"
    mkdir ./tmp
    PYO3_PYTHON=python3.14 CIBW_BUILD=1 python3.14 -m pip wheel --no-deps -w ./tmp/ ./
    for wheel in ./tmp/*.whl;
    do
      auditwheel repair "$wheel" --plat "manylinux2014_x86_64" -w ./
    done
  fi
fi
