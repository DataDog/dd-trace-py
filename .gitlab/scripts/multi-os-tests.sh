#!/bin/bash
# Multi-OS test script for Unix-like systems (Linux/macOS)
set -eo pipefail

# Install uv (retry up to 3 times)
for i in 1 2 3; do
  curl -LsSf https://astral.sh/uv/install.sh | sh && break
  echo "uv install attempt $i failed, retrying..."
  sleep 5
  [ "$i" -eq 3 ] && { echo "Failed to install uv after 3 attempts"; exit 1; }
done
export PATH="$HOME/.local/bin:$PATH"

# Create temp directory and install in isolation
export TMPDIR=$(mktemp -d)
cd "$TMPDIR"
export WHEEL_TAG="cp${PYTHON_VERSION//./}"
echo "Installing Python $PYTHON_VERSION and dependencies in $TMPDIR..."
uv python install $PYTHON_VERSION
uv venv --python $PYTHON_VERSION .venv
WHEEL_PATH=$(ls $CI_PROJECT_DIR/pywheels/ddtrace*${WHEEL_TAG}*${WHEEL_PATTERN} 2>/dev/null | head -1)
if [ -z "$WHEEL_PATH" ]; then
    echo "WARNING: No matching wheel found for tag '${WHEEL_TAG}' with pattern '${WHEEL_PATTERN}' in pywheels/"
    echo "Available files in pywheels/:"
    ls -la "$CI_PROJECT_DIR/pywheels/" 2>/dev/null || echo "  (directory does not exist)"
    echo ""
    echo "Skipping ${PLATFORM} tests: build job did not run in this pipeline."
    echo "This is expected for pipelines that do not include native code changes."
    exit 0
fi
uv pip install --python $PYTHON_VERSION -r "$CI_PROJECT_DIR/.gitlab/requirements/multi-os-tests.txt" $WHEEL_PATH

# Run tests
export PATH="$HOME/.local/bin:$PATH"
cd "$TMPDIR"
source .venv/bin/activate
echo "Running tests on $PLATFORM with Python $PYTHON_VERSION"
python -m pytest "$CI_PROJECT_DIR/tests/internal/service_name/test_extra_services_names.py" -v -s
python -m pytest "$CI_PROJECT_DIR/tests/appsec/architectures/test_appsec_loading_modules.py" -v -s
