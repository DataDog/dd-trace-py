#!/usr/bin/env bash -eu

set -eu

PREFIX=${1}
AUSTIN_VERSION="3.6"
K6_VERSION="0.26.2"

# Clean up existing installation
test -d $PREFIX && rm -rf $PREFIX || mkdir -p $PREFIX

if [[ "$OSTYPE" != "linux-gnu"* && "$OSTYPE" != "darwin"* ]]
then
    echo "Platform $OSTYPE not supported."
    exit 1
fi

# Create and activate the virtualenv
python -m venv ${PREFIX}
source ${PREFIX}/bin/activate
pip install pip --upgrade

# Install the application
cp -r scripts/profiles/flask-simple/app ${PREFIX}/app
pushd ${PREFIX}/app
    pip install -r requirements.txt
popd

# Install k6
pushd ${PREFIX}
    if [[ "$OSTYPE" == "linux-gnu"* ]]
    then
        curl -s https://github.com/loadimpact/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-linux64.tar.gz -L | tar xvz
    elif [[ "$OSTYPE" == "darwin"* ]]
    then
        curl -s https://github.com/loadimpact/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-mac.zip -L -o ${PREFIX}/k6.zip
        unzip k6.zip
        rm -f k6.zip
    fi
popd

# Install austin
pip install "austin-dist~=$AUSTIN_VERSION"

# Install ddtrace
pip install -e .

# Install diff-tool dependencies
pip install rich "austin-python~=1.4"
