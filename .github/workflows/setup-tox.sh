#!/bin/bash -eu

# This script is used to setup tox in a GitHub Actions workflow.
# Usage: . .github/workflows/setup-tox.sh <tox-env>

set -e
set -u

ENV=${1}

# Install tox
pip install tox

# Create the environment without running it
tox -e ${ENV} --notest

# Add pytest configuration for ddtrace
echo -e "[pytest]\nddtrace-patch-all = 1" > pytest.ini

# Enable the environment
source .tox/${ENV}/bin/activate

# Install ddtrace
pip install ../ddtrace
