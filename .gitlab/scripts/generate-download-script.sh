#!/usr/bin/env bash
set -euo pipefail

# Usage: generate-download-script.sh <s3_base_url>
# Example: generate-download-script.sh "https://dd-trace-py-builds.s3.amazonaws.com/main"
# Outputs download script to stdout

if [ $# -ne 1 ]; then
  echo "Usage: $0 <s3_base_url>" >&2
  exit 1
fi

S3_BASE_URL="$1"

if [ -z "${PACKAGE_VERSION:-}" ]; then
  echo "Error: PACKAGE_VERSION environment variable is not set." >&2
  exit 1
fi

# Generate download.sh and pipe through sed to replace placeholders
cat << 'DOWNLOAD_SCRIPT_EOF' | sed \
  -e "s|S3_URL_PLACEHOLDER|${S3_BASE_URL}|g" \
  -e "s|VERSION_PLACEHOLDER|${PACKAGE_VERSION}|g"
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
