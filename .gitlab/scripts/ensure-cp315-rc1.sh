#!/usr/bin/env bash
# Rebuild cp315 when the mirrored manylinux2014 image still ships 3.15.0b*.
# 3.15.0b1 SIGSEGVs during the native ddtrace wheel build; 3.15.0rc1+ does not.
# Drop this script once registry.ddbuild.io mirrors pypa manylinux2014 >= 2026.08.04-1.
set -euo pipefail

PY315=/opt/python/cp315-cp315/bin/python
if [[ ! -x "${PY315}" ]]; then
  echo "[ensure-cp315-rc1] ${PY315} not found" >&2
  exit 1
fi

if "${PY315}" -c 'import sys; sys.exit(0 if sys.version_info.releaselevel in ("candidate", "final") else 1)'; then
  echo "[ensure-cp315-rc1] cp315 already rc/final: $(${PY315} -V)"
  exit 0
fi

echo "[ensure-cp315-rc1] cp315 is pre-release ($(${PY315} -V)); rebuilding 3.15.0rc1..."
rm -rf /opt/python/cp315-cp315 /opt/_internal/cpython-3.15.0b*
/opt/_internal/build_scripts/build-cpython.sh \
  hugo@python.org https://github.com/login/oauth 3.15.0rc1
/opt/_internal/build_scripts/finalize-one.sh /opt/_internal/cpython-3.15.0rc1
/opt/python/cp315-cp315/bin/python -V
