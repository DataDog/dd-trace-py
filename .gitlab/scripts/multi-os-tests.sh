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
PYWHEELS_DIR="${CI_PROJECT_DIR}/pywheels"
WHEEL_GLOB="ddtrace*${WHEEL_TAG}*${WHEEL_PATTERN}"
if [ ! -d "${PYWHEELS_DIR}" ]; then
  echo "No wheel found matching ${PYWHEELS_DIR}/${WHEEL_GLOB}"
  echo "Directory ${PYWHEELS_DIR} does not exist (build macos arm64 artifacts may be missing)."
  echo "Contents of ${CI_PROJECT_DIR}:"
  ls -la "${CI_PROJECT_DIR}/" 2>/dev/null || true
  exit 1
fi
WHEEL_PATH=$(find "${PYWHEELS_DIR}" -maxdepth 1 -type f -name "${WHEEL_GLOB}" 2>/dev/null | sort | head -n 1 || true)
if [ -z "${WHEEL_PATH}" ]; then
  echo "No wheel found matching ${PYWHEELS_DIR}/${WHEEL_GLOB}"
  echo "Contents of ${PYWHEELS_DIR}:"
  ls -la "${PYWHEELS_DIR}/" 2>/dev/null || echo "(directory empty or unreadable)"
  exit 1
fi
echo "Using wheel: ${WHEEL_PATH}"
uv pip install --python $PYTHON_VERSION -r "$CI_PROJECT_DIR/.gitlab/requirements/multi-os-tests.txt" "${WHEEL_PATH}"

# Run tests
export PATH="$HOME/.local/bin:$PATH"
cd "$TMPDIR"
source .venv/bin/activate
echo "Running tests on $PLATFORM with Python $PYTHON_VERSION"
python -m pytest "$CI_PROJECT_DIR/tests/internal/service_name/test_extra_services_names.py" -v -s
python -m pytest "$CI_PROJECT_DIR/tests/appsec/architectures/test_appsec_loading_modules.py" -v -s
