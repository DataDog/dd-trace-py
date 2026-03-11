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
  python3.9 - <<'PY'
from pathlib import Path

setup_py = Path("setup.py")
legacy_import = "from pkg_resources import get_build_platform  # isort: skip"
compat_import = "get_build_platform = sysconfig.get_platform"

contents = setup_py.read_text()
if legacy_import in contents:
    setup_py.write_text(contents.replace(legacy_import, compat_import))
    print("Patched baseline setup.py to avoid pkg_resources dependency")
PY
  mkdir ./tmp
  PYO3_PYTHON=python3.9 CIBW_BUILD=1 python3.9 -m pip wheel --no-deps -w ./tmp/ ./
  for wheel in ./tmp/*.whl;
  do
    auditwheel repair "$wheel" --plat "manylinux2014_x86_64" -w ./
  done
fi
