#!/usr/bin/env bash

set -e -o pipefail

VERSION=${1:-311}

source venv${VERSION}/bin/activate

latest_wheel=$(ls -t dist/ddtrace*$VERSION*.whl ddtrace*$VERSION*.whl | head -n 1)
echo "Installing $latest_wheel"

pip uninstall -y ddtrace
pip install $latest_wheel
