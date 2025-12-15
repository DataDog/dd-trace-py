#!/bin/bash
# Multi-OS test script for Unix-like systems (Linux/macOS)
set -e

# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Create temp directory and install in isolation
export TMPDIR=$(mktemp -d)
cd "$TMPDIR"
export WHEEL_TAG="cp${PYTHON_VERSION//./}"
echo "Installing Python $PYTHON_VERSION and dependencies in $TMPDIR..."
uv python install $PYTHON_VERSION
uv venv --python $PYTHON_VERSION .venv
WHEEL_PATH="$CI_PROJECT_DIR/pywheels/ddtrace*${WHEEL_TAG}*${WHEEL_PATTERN}"
uv pip install --python $PYTHON_VERSION -r "$CI_PROJECT_DIR/.gitlab/requirements/multi-os-tests.txt" $WHEEL_PATH

# Run tests
export PATH="$HOME/.local/bin:$PATH"
cd "$TMPDIR"
source .venv/bin/activate
echo "Running tests on $PLATFORM with Python $PYTHON_VERSION"
python -m pytest "$CI_PROJECT_DIR/tests/internal/service_name/test_extra_services_names.py" -v -s
python -m pytest "$CI_PROJECT_DIR/tests/appsec/architectures/test_appsec_loading_modules.py" -v -s
