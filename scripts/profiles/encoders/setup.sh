#!/usr/bin/env bash -eu

set -eu

if [[ "$OSTYPE" != "linux-gnu"* ]]
then
    echo "Platform $OSTYPE not supported."
    exit 1
fi

PREFIX=${1}

# Clean up existing installation
test -d $PREFIX && rm -rf $PREFIX || mkdir -p $PREFIX

# Create and activate the virtualenv
python3.8 -m venv ${PREFIX}
source ${PREFIX}/bin/activate
pip install pip --upgrade

# Install austin
pushd ${PREFIX}
    sudo apt-get -y install libunwind-dev binutils-dev libiberty-dev
    git clone --depth=1 https://github.com/p403n1x87/austin.git -b devel
    pushd austin
        gcc -O3 -Os -s -Wall -pthread src/*.c -DAUSTINP -lunwind-ptrace -lunwind-generic -lbfd -o src/austinp
        mv src/austinp ${PREFIX}/austinp
        chmod +x ${PREFIX}/austinp
    popd
popd

# Install dependencies
pip install hypothesis msgpack pytest

# Install ddtrace
pip install -e .
