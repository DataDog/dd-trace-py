#!/bin/bash
set -euo pipefail

# Validate release inputs and extract PACKAGE_VERSION from pyproject.toml
#
# Expected environment variables:
#   TARGET_SHA: Git SHA to be released (e.g. fa5ce134fe59644c8906604eec2f023f4c1d4129)
#   PACKAGE_VERSION: (Optional) Override version for testing. If not set, extracted from pyproject.toml
#
# Outputs:
#   PACKAGE_VERSION environment variable (either provided or extracted from pyproject.toml at TARGET_SHA)
#   Validates that PACKAGE_VERSION matches semver format

TARGET_SHA="${TARGET_SHA:?TARGET_SHA not set}"

echo "Validating release inputs..."
echo ""

# Check TARGET_SHA
if [ -z "$TARGET_SHA" ]; then
  echo "❌ ERROR: TARGET_SHA is required"
  exit 1
fi
if ! echo "$TARGET_SHA" | grep -qE '^[0-9a-f]{40}$'; then
  echo "❌ ERROR: TARGET_SHA must be a valid 40-character hex SHA (got: $TARGET_SHA)"
  exit 1
fi
echo "✓ TARGET_SHA: $TARGET_SHA"

# Extract PACKAGE_VERSION from pyproject.toml at TARGET_SHA using Python TOML parser
# (Skip if PACKAGE_VERSION is already set via pipeline variable - useful for testing)
if [ -z "${PACKAGE_VERSION:-}" ]; then
  echo "Extracting package version from pyproject.toml..."
  PACKAGE_VERSION=$(git show $TARGET_SHA:pyproject.toml | python3 -c '
import sys, tomllib
try:
    config = tomllib.loads(sys.stdin.read())
    print(config["project"]["version"])
except KeyError:
    print("ERROR: Could not find project.version in pyproject.toml", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"ERROR: Failed to parse pyproject.toml: {e}", file=sys.stderr)
    sys.exit(1)
')
else
  echo "Using provided PACKAGE_VERSION (override mode)"
fi

if [ -z "${PACKAGE_VERSION:-}" ]; then
  echo "❌ ERROR: Could not extract version from pyproject.toml at TARGET_SHA"
  exit 1
fi

if ! echo "$PACKAGE_VERSION" | grep -qE '^[0-9]+\.[0-9]+\.[0-9]+(rc[0-9]+)?$'; then
  echo "❌ ERROR: PACKAGE_VERSION must match semver format (x.y.z or x.y.zrcN) (got: $PACKAGE_VERSION)"
  exit 1
fi
echo "✓ PACKAGE_VERSION: $PACKAGE_VERSION"

echo ""
echo "✅ All inputs validated"

# Output PACKAGE_VERSION for CI job to capture
echo "$PACKAGE_VERSION"
