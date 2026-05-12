#!/usr/bin/env bash
set -euo pipefail

# Usage: generate-index-html.sh
# Outputs PEP 503-style index HTML to stdout.
# Reads GitLab CI environment variables for build metadata (all optional):
#   CI_COMMIT_SHA, CI_COMMIT_SHORT_SHA, CI_COMMIT_REF_NAME,
#   CI_PIPELINE_ID, PACKAGE_VERSION

# Find all wheels
WHEELS=(pywheels/*.whl)
if [ ${#WHEELS[@]} -eq 0 ]; then
  echo "ERROR: No wheels found in pywheels/" >&2
  exit 1
fi

# HTML-escape a string so it is safe to embed in tag content or attribute values.
# Uses sed because bash pattern replacement treats & as a backreference on some versions.
html_escape() {
  printf '%s' "$1" | sed \
    -e 's/&/\&amp;/g' \
    -e 's/</\&lt;/g' \
    -e 's/>/\&gt;/g' \
    -e 's/"/\&quot;/g' \
    -e "s/'/\&#39;/g"
}

# Collect build metadata (fall back gracefully when run outside CI)
COMMIT_SHA="$(html_escape "${CI_COMMIT_SHA:-unknown}")"
COMMIT_SHORT="$(html_escape "${CI_COMMIT_SHORT_SHA:-${CI_COMMIT_SHA:0:8}}")"
REF_NAME="$(html_escape "${CI_COMMIT_REF_NAME:-unknown}")"
PIPELINE_ID="$(html_escape "${CI_PIPELINE_ID:-unknown}")"
PKG_VERSION="$(html_escape "${PACKAGE_VERSION:-unknown}")"
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

cat << HTML
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ddtrace ${PKG_VERSION} — ${REF_NAME}</title>
  <meta name="dd-commit-sha"       content="${COMMIT_SHA}">
  <meta name="dd-ref-name"         content="${REF_NAME}">
  <meta name="dd-pipeline-id"      content="${PIPELINE_ID}">
  <meta name="dd-package-version"  content="${PKG_VERSION}">
  <meta name="dd-created-at"       content="${CREATED_AT}">
  <style>
    body { font-family: monospace; max-width: 900px; margin: 2em auto; padding: 0 1em; }
    table { border-collapse: collapse; margin-bottom: 1.5em; }
    td { padding: 0.15em 1em 0.15em 0; }
    td:first-child { color: #666; }
    hr { border: none; border-top: 1px solid #ccc; margin: 1.5em 0; }
    a { display: block; padding: 0.1em 0; }
  </style>
</head>
<body>
  <h2>ddtrace build artifacts</h2>
  <table>
    <tr><td>Version</td>     <td>${PKG_VERSION}</td></tr>
    <tr><td>Branch / ref</td><td>${REF_NAME}</td></tr>
    <tr><td>Commit</td>      <td>${COMMIT_SHA}</td></tr>
    <tr><td>Pipeline</td>    <td>${PIPELINE_ID}</td></tr>
    <tr><td>Created</td>     <td>${CREATED_AT}</td></tr>
  </table>
  <hr>
HTML

for w in "${WHEELS[@]}"; do
  fname="$(basename "$w")"
  enc_fname="${fname//+/%2B}"
  esc_fname="$(html_escape "$fname")"
  echo "  <a href=\"${enc_fname}\">${esc_fname}</a>"
done

echo "</body></html>"
