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

# If no local wheel, download from the last successful build on main
if [ -z "$WHEEL_PATH" ] && [ "$PLATFORM" = "macOS" ]; then
    echo "No local wheel found. Downloading from last successful main pipeline..."
    mkdir -p "$CI_PROJECT_DIR/pywheels"
    ENCODED_JOB=$(python3 -c "import urllib.parse; print(urllib.parse.quote('build macos'))")
    curl -fsSL --header "JOB-TOKEN: $CI_JOB_TOKEN" \
      -o /tmp/wheels.zip \
      "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/jobs/artifacts/main/download?job=${ENCODED_JOB}"
    unzip -o /tmp/wheels.zip -d "$CI_PROJECT_DIR/pywheels"
    # Flatten nested pywheels/pywheels/ structure from artifact zip
    find "$CI_PROJECT_DIR/pywheels" -mindepth 2 -name "*.whl" -exec mv {} "$CI_PROJECT_DIR/pywheels/" \;
    WHEEL_PATH=$(ls $CI_PROJECT_DIR/pywheels/ddtrace*${WHEEL_TAG}*${WHEEL_PATTERN} 2>/dev/null | head -1)
fi

if [ -z "$WHEEL_PATH" ]; then
    echo "ERROR: No wheel found for tag '${WHEEL_TAG}' with pattern '${WHEEL_PATTERN}'."
    ls -la "$CI_PROJECT_DIR/pywheels/" 2>/dev/null || echo "pywheels/ does not exist"
    exit 1
fi

echo "Using wheel: $WHEEL_PATH"
uv pip install --python $PYTHON_VERSION -r "$CI_PROJECT_DIR/.gitlab/requirements/multi-os-tests.txt" $WHEEL_PATH

# Run tests
export PATH="$HOME/.local/bin:$PATH"
cd "$TMPDIR"
source .venv/bin/activate
echo "Running tests on $PLATFORM with Python $PYTHON_VERSION"
python -m pytest "$CI_PROJECT_DIR/tests/internal/service_name/test_extra_services_names.py" -v -s
python -m pytest "$CI_PROJECT_DIR/tests/appsec/architectures/test_appsec_loading_modules.py" -v -s
