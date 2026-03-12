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
  # Historical commits import pkg_resources which was removed in setuptools>=81.
  # Constrain the build environment so those old setup.py files keep working.
  echo "setuptools<81" > /tmp/baseline-build-constraints.txt
  mkdir ./tmp
  PIP_CONSTRAINT=/tmp/baseline-build-constraints.txt \
    PYO3_PYTHON=python3.9 CIBW_BUILD=1 python3.9 -m pip wheel --no-deps -w ./tmp/ ./
  for wheel in ./tmp/*.whl;
  do
    auditwheel repair "$wheel" --plat "manylinux2014_x86_64" -w ./
  done
fi
