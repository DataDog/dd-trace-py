#!/usr/bin/env bash -eu

set -eu

if [[ "$OSTYPE" != "linux-gnu"* ]]
then
    echo "Platform $OSTYPE not supported."
    exit 1
fi

PREFIX=${1}

test -d ${PREFIX}/artifacts && rm -rf ${PREFIX}/artifacts || mkdir -p ${PREFIX}/artifacts

function profile {
    austinp -bso ${PREFIX}/artifacts/api.mojo python scripts/profiles/di-low-impact-monitoring-api/run.py
    LC_ALL=C sed -i 's|/home/runner/work/dd-trace-py/dd-trace-py/ddtrace/||g' ${PREFIX}/artifacts/api.mojo
    austinp-resolve ${PREFIX}/artifacts/api.mojo ${PREFIX}/artifacts/api.resolved.mojo || true
}

source ${PREFIX}/bin/activate

profile

deactivate
