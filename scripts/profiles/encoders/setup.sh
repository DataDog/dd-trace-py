#!/usr/bin/env bash -eu

set -eu

if [[ "$OSTYPE" != "linux-gnu"* ]]; then
	echo "Platform $OSTYPE not supported."
	exit 1
fi

PREFIX=${1}

# Clean up existing installation
test -d $PREFIX && rm -rf $PREFIX || mkdir -p $PREFIX

# Create and activate the virtualenv
python3.10 -m venv ${PREFIX}
source ${PREFIX}/bin/activate
pip install pip --upgrade

# Install dependencies
pip install hypothesis msgpack pytest mock austin-python~=1.7 austin-dist~=3.6

# Install ddtrace
pip install -e .
