#!/usr/bin/env bash
# Uploads ELF debug symbols to the Datadog symbols backend for crashtracker symbolication.
#
# Extracts .debug files from debugwheelhouse/*.zip and uploads them using datadog-ci.
# Uploads to both prod (datadoghq.com) and preprod (datad0g.com).
#
# API keys are fetched from AWS SSM (same pattern as PyPI token and quality gate keys):
#   ci.dd-trace-py.dd-public-symbol-api-key   — Datadog prod API key for symbol uploads
#   ci.dd-trace-py.dd-preprod-symbol-api-key   — Datadog preprod API key for symbol uploads

set -euo pipefail

# Fetch API keys from AWS SSM (non-fatal so a missing key doesn't block the other upload)
DD_PUBLIC_SYMBOL_API_KEY=$(aws ssm get-parameter --region us-east-1 --name "ci.dd-trace-py.dd-public-symbol-api-key" --with-decryption --query "Parameter.Value" --out text 2>/dev/null || true)
DD_PREPROD_SYMBOL_API_KEY=$(aws ssm get-parameter --region us-east-1 --name "ci.dd-trace-py.dd-preprod-symbol-api-key" --with-decryption --query "Parameter.Value" --out text 2>/dev/null || true)

# Create a temporary directory to extract debug symbols
SYMBOLS_DIR=$(mktemp -d)
trap "rm -rf '${SYMBOLS_DIR}'" EXIT

# Extract .debug files from all debug symbol packages
shopt -s nullglob
SYMBOL_PACKAGES=(debugwheelhouse/*.zip)

if [ ${#SYMBOL_PACKAGES[@]} -eq 0 ]; then
  echo "No debug symbol packages found in debugwheelhouse/"
  exit 0
fi

echo "Extracting debug symbols from ${#SYMBOL_PACKAGES[@]} package(s)"
for pkg in "${SYMBOL_PACKAGES[@]}"; do
  echo "  Extracting: $(basename "$pkg")"
  python3 -m zipfile -e "$pkg" "${SYMBOLS_DIR}"
done

# Count extracted .debug files
DEBUG_FILES=$(find "${SYMBOLS_DIR}" -name "*.debug" -type f | wc -l)
echo "Found ${DEBUG_FILES} .debug file(s) to upload"

if [ "${DEBUG_FILES}" -eq 0 ]; then
  echo "No .debug files found after extraction, skipping upload"
  exit 0
fi

# Upload to prod
if [ -n "${DD_PUBLIC_SYMBOL_API_KEY:-}" ]; then
  echo "Uploading debug symbols to prod (datadoghq.com)..."
  DATADOG_API_KEY="${DD_PUBLIC_SYMBOL_API_KEY}" \
    DD_BETA_COMMANDS_ENABLED=1 \
    datadog-ci elf-symbols upload "${SYMBOLS_DIR}"
else
  echo "DD_PUBLIC_SYMBOL_API_KEY not set, skipping prod upload"
fi

# Upload to preprod
if [ -n "${DD_PREPROD_SYMBOL_API_KEY:-}" ]; then
  echo "Uploading debug symbols to preprod (datad0g.com)..."
  DATADOG_API_KEY="${DD_PREPROD_SYMBOL_API_KEY}" \
    DATADOG_SITE="datad0g.com" \
    DD_BETA_COMMANDS_ENABLED=1 \
    datadog-ci elf-symbols upload "${SYMBOLS_DIR}"
else
  echo "DD_PREPROD_SYMBOL_API_KEY not set, skipping preprod upload"
fi

echo "Debug symbol upload complete"
