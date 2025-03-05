#!/usr/bin/env bash
set -e -o pipefail

# If we have a tag (e.g. v2.21.1), then use the PyPI published wheel
# Otherwise, build the wheel from commit hash
if [[ -n "${BASELINE_TAG}" ]];
then
  python3.9 -m pip download --no-deps "ddtrace==${BASELINE_TAG:1}"
else
  ulimit -c unlimited
  curl -sSf https://sh.rustup.rs | sh -s -- -y;
  export PATH="$HOME/.cargo/bin:$PATH"
  echo "Building wheel for ${BASELINE_BRANCH}:${BASELINE_COMMIT_SHA}"
  git checkout "${BASELINE_COMMIT_SHA}"
  mkdir ./tmp
  python3.9 -m pip wheel --no-deps -w ./tmp/ ./
  for wheel in ./tmp/*.whl;
  do
    auditwheel repair "$wheel" --plat "manylinux2014_x86_64" -w ./
  done
fi
