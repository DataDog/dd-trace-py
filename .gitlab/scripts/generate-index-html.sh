#!/usr/bin/env bash
set -euo pipefail

# Usage: generate-index-html.sh
# Outputs PEP 503-style index HTML to stdout

# Find all wheels
WHEELS=(pywheels/*.whl)
if [ ${#WHEELS[@]} -eq 0 ]; then
  echo "ERROR: No wheels found in pywheels/" >&2
  exit 1
fi

# Generate index.html to stdout
echo '<!DOCTYPE html><html lang="en"><body>'
for w in "${WHEELS[@]}"; do
  fname="$(basename "$w")"
  # URL-encode special characters (especially +)
  enc_fname="${fname//+/%2B}"
  echo "<a href=\"${enc_fname}\">${fname}</a><br>"
done
echo "</body></html>"
