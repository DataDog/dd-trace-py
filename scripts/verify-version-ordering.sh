#!/bin/bash
set -euo pipefail

# Verify version ordering for a release
#
# Expected environment variables:
#   TARGET_SHA: Git SHA being released (used for context only)
#   EXPECTED_VERSION: Version to be released (e.g., "4.1.0", "4.1.0rc1")
#
# This script:
# 1. Gets all version tags from the repository
# 2. Calls the Python validation script
# 3. Reports success or failure with helpful messages

TARGET_SHA="${TARGET_SHA:?TARGET_SHA not set}"
PACKAGE_VERSION="${PACKAGE_VERSION:?PACKAGE_VERSION not set}"

echo "Verifying version ordering for release $PACKAGE_VERSION @ $TARGET_SHA"
echo ""

# Validate TARGET_SHA exists
echo "Validating TARGET_SHA..."
if ! git rev-parse "$TARGET_SHA" &>/dev/null; then
  echo "  ❌ FAILED: TARGET_SHA ($TARGET_SHA) is not a valid commit"
  exit 1
fi
echo "  ✓ TARGET_SHA is valid"
echo ""

# Get all version tags from repository
echo "Fetching all version tags from repository..."
all_tags=$(git tag -l "v*.*.*" 2>/dev/null || true)

# Filter to valid semver format tags (v*.*.* or v*.*.*.rcN)
version_tags=$(echo "$all_tags" | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$' || true)

if [ -z "$version_tags" ]; then
  echo "  ❌ FAILED: No version tags found in repository"
  echo "  This should be impossible - the repository should have existing releases"
  exit 1
fi

# Strip 'v' prefix from tags to get version strings
existing_versions=$(echo "$version_tags" | sed 's/^v//' | sort -V)
echo "  Found $(echo "$existing_versions" | wc -l) version tags"
echo "  Latest tag overall: $(echo "$version_tags" | sort -V | tail -1)"
echo ""

# Get most recent tag in TARGET_SHA's commit history
echo "Checking TARGET_SHA's commit history..."
git_history_most_recent_tag=$(git tag --merged "$TARGET_SHA" -l "v*.*.*" 2>/dev/null | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$' | sort -V | tail -1 || true)

if [ -z "$git_history_most_recent_tag" ]; then
  echo "  ❌ FAILED: No version tags found in TARGET_SHA's commit history"
  echo "  (This can happen if TARGET_SHA is not an ancestor of any tag, or if the branch is new)"
  exit 1
fi

git_history_most_recent=$(echo "$git_history_most_recent_tag" | sed 's/^v//')
echo "  Most recent tag in history: $git_history_most_recent_tag"
echo ""

# Call Python validation script
echo "Validating version sequence..."
# Get script directory and build command with all existing versions as arguments
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
python3 "$script_dir/verify-version-ordering.py" "$PACKAGE_VERSION" --git-history-most-recent "$git_history_most_recent" $existing_versions 2>&1
validation_exit=$?

if [ $validation_exit -eq 0 ]; then
  echo ""
  echo "✅ Version ordering verified"
  exit 0
else
  echo ""
  echo "❌ Version ordering validation failed"
  exit 1
fi
