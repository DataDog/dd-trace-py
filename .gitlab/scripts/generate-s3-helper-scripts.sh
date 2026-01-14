#!/usr/bin/env bash
set -euo pipefail

# Usage: generate-s3-helper-scripts.sh <s3_base_url> <output_prefix>
# Example: generate-s3-helper-scripts.sh "https://dd-trace-py-builds.s3.amazonaws.com/main" "main"

if [ $# -ne 2 ]; then
  echo "Usage: $0 <s3_base_url> <output_prefix>" >&2
  exit 1
fi

S3_BASE_URL="$1"
OUTPUT_PREFIX="$2"

if [ -z "${PACKAGE_VERSION:-}" ]; then
  echo "Error: PACKAGE_VERSION environment variable is not set." >&2
  exit 1
fi
echo "Detected version: ${PACKAGE_VERSION}"

# Generate download.sh
cat > "${OUTPUT_PREFIX}.download.sh" << 'DOWNLOAD_SCRIPT_EOF'
#!/usr/bin/env bash
set -euo pipefail

# Parse arguments
DEST_DIR="."
PIP_ARGS=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --dest)
      DEST_DIR="$2"
      shift 2
      ;;
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
      echo "Usage: $0 [--dest DIR] [--python-version VERSION] [--platform PLATFORM]" >&2
      exit 1
      ;;
  esac
done

# Download wheel
echo "Downloading ddtrace==VERSION_PLACEHOLDER from S3_URL_PLACEHOLDER/index.html"
pip download --no-index --no-deps \
  --find-links S3_URL_PLACEHOLDER/index.html \
  ddtrace==VERSION_PLACEHOLDER \
  $PIP_ARGS \
  -d "${DEST_DIR}"

echo "Downloaded to ${DEST_DIR}"
DOWNLOAD_SCRIPT_EOF

# Replace placeholders in download.sh
sed -i.bak \
  -e "s|S3_URL_PLACEHOLDER|${S3_BASE_URL}|g" \
  -e "s|VERSION_PLACEHOLDER|${PACKAGE_VERSION}|g" \
  "${OUTPUT_PREFIX}.download.sh"
rm "${OUTPUT_PREFIX}.download.sh.bak"

# Generate install.sh
cat > "${OUTPUT_PREFIX}.install.sh" << 'INSTALL_SCRIPT_EOF'
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
echo "Downloading ddtrace==VERSION_PLACEHOLDER from S3_URL_PLACEHOLDER/index.html"
pip download --no-index --no-deps \
  --find-links S3_URL_PLACEHOLDER/index.html \
  ddtrace==VERSION_PLACEHOLDER \
  $PIP_ARGS \
  -d "${TMP_DIR}"

echo "Installing ddtrace==VERSION_PLACEHOLDER"
pip install "${TMP_DIR}"/ddtrace-*.whl

echo "Successfully installed ddtrace==VERSION_PLACEHOLDER"
INSTALL_SCRIPT_EOF

# Replace placeholders in install.sh
sed -i.bak \
  -e "s|S3_URL_PLACEHOLDER|${S3_BASE_URL}|g" \
  -e "s|VERSION_PLACEHOLDER|${PACKAGE_VERSION}|g" \
  "${OUTPUT_PREFIX}.install.sh"
rm "${OUTPUT_PREFIX}.install.sh.bak"

echo "Generated ${OUTPUT_PREFIX}.download.sh and ${OUTPUT_PREFIX}.install.sh"
