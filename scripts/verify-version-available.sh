#!/bin/bash
set -euo pipefail

# Verify that a release version is not already published
#
# Expected environment variables:
#   PACKAGE_VERSION: Version to check (e.g. 4.1.0, 4.1.0rc1)
#   GITHUB_REPO: GitHub repository (owner/repo)
#   GH_TOKEN: GitHub token (for API access)
#
# This script checks that the version has not been released in:
# - GitHub releases
# - Git tags
# - PyPI
#
# Fails if the version is found in any of these locations.

PACKAGE_VERSION="${PACKAGE_VERSION:?PACKAGE_VERSION not set}"
GITHUB_REPO="${GITHUB_REPO:?GITHUB_REPO not set}"
GH_TOKEN="${GH_TOKEN:?GH_TOKEN not set}"

export GH_TOKEN

echo "Verifying release version $PACKAGE_VERSION is available..."
echo ""

all_available=true

# Check GitHub Release (GitHub releases are tagged with v prefix)
echo "Checking GitHub releases for v$PACKAGE_VERSION..."
if gh release view "v$PACKAGE_VERSION" --repo "$GITHUB_REPO" &>/dev/null; then
  echo "  ❌ FAILED: Release v$PACKAGE_VERSION already exists on GitHub"
  all_available=false
else
  echo "  ✓ Version not found in GitHub releases"
fi

echo ""

# Check Git Tag (uses version with v prefix)
echo "Checking git tags for v$PACKAGE_VERSION..."
if gh api repos/"$GITHUB_REPO"/git/refs/tags/v"$PACKAGE_VERSION" &>/dev/null; then
  echo "  ❌ FAILED: Tag v$PACKAGE_VERSION already exists in git"
  all_available=false
else
  echo "  ✓ Version not found in git tags"
fi

echo ""

# Check PyPI (uses version without v)
echo "Checking PyPI for ddtrace version $PACKAGE_VERSION..."

# Query PyPI JSON API
pypi_response=$(curl -s "https://pypi.org/pypi/ddtrace/$PACKAGE_VERSION/json" 2>/dev/null || echo "{}")

if echo "$pypi_response" | jq -e '.info.version' &>/dev/null; then
  echo "  ❌ FAILED: Version $PACKAGE_VERSION already exists on PyPI"
  all_available=false
else
  echo "  ✓ Version not found on PyPI"
fi

echo ""

if [ "$all_available" = true ]; then
  echo "✅ Release version $PACKAGE_VERSION is available!"
  exit 0
else
  echo "❌ FAILED: Release version $PACKAGE_VERSION is not available (already released or in progress)"
  exit 1
fi
