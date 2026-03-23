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
    echo "No local wheel found for tag '${WHEEL_TAG}' with pattern '${WHEEL_PATTERN}'."
    echo "Downloading wheels from the last successful main pipeline..."

    # Determine the build job name for downloading artifacts from main
    if [ "$PLATFORM" = "macOS" ]; then
        JOB_NAME="build macos"
    fi

    if [ -n "$JOB_NAME" ] && [ -n "$CI_JOB_TOKEN" ]; then
        mkdir -p "$CI_PROJECT_DIR/pywheels"
        ENCODED_JOB=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$JOB_NAME'))")
        API_URL="${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/jobs/artifacts/main/download?job=${ENCODED_JOB}"
        echo "Fetching from: $API_URL"
        if curl -fsSL --header "JOB-TOKEN: $CI_JOB_TOKEN" -o /tmp/wheels.zip "$API_URL" 2>/dev/null; then
            cd "$CI_PROJECT_DIR/pywheels"
            unzip -o /tmp/wheels.zip 2>/dev/null || true
            # Flatten nested directories
            find . -name "*.whl" -not -path "./*.whl" -exec mv {} . \; 2>/dev/null || true
            rm -f /tmp/wheels.zip
            cd "$TMPDIR"
            WHEEL_PATH=$(ls $CI_PROJECT_DIR/pywheels/ddtrace*${WHEEL_TAG}*${WHEEL_PATTERN} 2>/dev/null | head -1)
        else
            echo "Failed to download artifacts from main pipeline."
        fi
    fi
fi
if [ -z "$WHEEL_PATH" ]; then
    echo "WARNING: No wheel available for tag '${WHEEL_TAG}' with pattern '${WHEEL_PATTERN}'."
    echo "Available files in pywheels/:"
    ls -la "$CI_PROJECT_DIR/pywheels/" 2>/dev/null || echo "  (directory does not exist)"
    echo ""
    echo "Skipping ${PLATFORM} tests: no compatible wheel found."
    exit 0
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
