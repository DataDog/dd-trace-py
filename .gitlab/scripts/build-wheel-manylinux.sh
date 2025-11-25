#!/usr/bin/env bash
set -euo pipefail

export PYTHON_TAG=cp314-cp314
export CIBW_BUILD=1
export CMAKE_ARGS="-DNATIVE_TESTING=OFF"

# Setup Python environment
manylinux-interpreters ensure "${PYTHON_TAG}"
export PATH="/opt/python/${PYTHON_TAG}/bin:${PATH}"
which python
python --version
which pip
pip --version

# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source $HOME/.cargo/env

# Build wheel
python -m build --wheel --outdir pywheels .
