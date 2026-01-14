#!/usr/bin/env bash
set -euo pipefail

# Usage: generate-index-html.sh <output_prefix>
# Example: generate-index-html.sh "commit"

if [ $# -ne 1 ]; then
  echo "Usage: $0 <output_prefix>" >&2
  exit 1
fi

OUTPUT_PREFIX="$1"

# Find all wheels
WHEELS=(pywheels/*.whl)
if [ ${#WHEELS[@]} -eq 0 ]; then
  echo "ERROR: No wheels found in pywheels/" >&2
  exit 1
fi

# Generate index.html
{
  echo '<!DOCTYPE html><html lang="en"><body>'
  for w in "${WHEELS[@]}"; do
    fname="$(basename "$w")"
    # URL-encode special characters (especially +)
    enc_fname="${fname//+/%2B}"
    echo "<a href=\"${enc_fname}\">${fname}</a><br>"
  done
  echo "</body></html>"
} > "${OUTPUT_PREFIX}.index.html"

echo "Generated ${OUTPUT_PREFIX}.index.html"
