#!/usr/bin/env bash

set -ex

if [[ "${CIRCLECI}" = "true" ]]; then
    echo "Skipping install"
elif [[ "${READTHEDOCS}" = "True" ]]; then
    # We skip here because we do not check spelling in RTD
    echo "Skipping install"
else
    export DEBIAN_FRONTEND="noninteractive"
    export DEBCONF_NOWARNINGS="yes"

    apt-get -qq update
    apt-get -qy install --no-install-recommends libenchant-2-dev >/dev/null ||
        apt-get -qy install --no-install-recommends libenchant-dev >/dev/null
fi
