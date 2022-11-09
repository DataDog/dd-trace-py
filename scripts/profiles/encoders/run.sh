#!/usr/bin/env bash -eu

set -eu

if [[ "$OSTYPE" != "linux-gnu"* ]]
then
    echo "Platform $OSTYPE not supported."
    exit 1
fi

PREFIX=${1}

AUSTIN_INTERVAL=1ms
AUSTIN_EXPOSURE=4  # seconds

test -d ${PREFIX}/artifacts && rm -rf ${PREFIX}/artifacts || mkdir -p ${PREFIX}/artifacts

function profile {
    ver=${1}

    PYTHONPATH="." python scripts/profiles/encoders/run.py ${ver} &
    sleep 2
    sudo ${PREFIX}/austinp -bsi ${AUSTIN_INTERVAL} -x ${AUSTIN_EXPOSURE} -o ${PREFIX}/artifacts/${ver/./_}.mojo -p $!
    austinp-resolve ${PREFIX}/artifacts/${ver/./_}.mojo ${PREFIX}/artifacts/${ver/./_}.resolved.mojo || true
}

source ${PREFIX}/bin/activate

profile "v0.3"
profile "v0.5"

sudo chown -R $(id -u):$(id -g) ${PREFIX}/artifacts/*
