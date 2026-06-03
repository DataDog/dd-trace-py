#!/usr/bin/env bash
set -euo pipefail

# Usage: generate-install-script.sh <s3_base_url> [index_filename]
# Example: generate-install-script.sh "https://dd-trace-py-builds.s3.amazonaws.com/main"
# Example: generate-install-script.sh "https://dd-trace-py-builds.s3.amazonaws.com/12345" "index-manylinux2014.html"
# Outputs install script to stdout

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "Usage: $0 <s3_base_url> [index_filename]" >&2
  exit 1
fi

S3_BASE_URL="$1"
INDEX_FILE="${2:-index.html}"

if [ -z "${PACKAGE_VERSION:-}" ]; then
  echo "Error: PACKAGE_VERSION environment variable is not set." >&2
  exit 1
fi

# Generate install.sh and pipe through sed to replace placeholders
cat << 'INSTALL_SCRIPT_EOF' | sed \
  -e "s|S3_URL_PLACEHOLDER|${S3_BASE_URL}|g" \
  -e "s|VERSION_PLACEHOLDER|${PACKAGE_VERSION}|g" \
  -e "s|INDEX_FILE_PLACEHOLDER|${INDEX_FILE}|g"
#!/usr/bin/env bash
set -euo pipefail

# Parse arguments
PIP_ARGS=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --python-version)
      PIP_ARGS="$PIP_ARGS --python-version $2"
      shift 2
      ;;
    --platform)
      PIP_ARGS="$PIP_ARGS --platform $2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      echo "Usage: $0 [--python-version VERSION] [--platform PLATFORM]" >&2
      exit 1
      ;;
  esac
done

# Create temp directory and ensure cleanup
TMP_DIR=$(mktemp -d)
trap "rm -rf '${TMP_DIR}'" EXIT

# Download and install
echo "Downloading ddtrace==VERSION_PLACEHOLDER from S3_URL_PLACEHOLDER/INDEX_FILE_PLACEHOLDER"
python3 -m pip download --no-index --no-deps \
  --find-links S3_URL_PLACEHOLDER/INDEX_FILE_PLACEHOLDER \
  ddtrace==VERSION_PLACEHOLDER \
  $PIP_ARGS \
  -d "${TMP_DIR}"

echo "Installing ddtrace==VERSION_PLACEHOLDER"
python3 -m pip install "${TMP_DIR}"/ddtrace-*.whl

echo "Successfully installed ddtrace==VERSION_PLACEHOLDER"
INSTALL_SCRIPT_EOF
