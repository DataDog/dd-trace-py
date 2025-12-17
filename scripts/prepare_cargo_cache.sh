#!/usr/bin/env bash
# scripts/prepare_cargo_cache.sh
# Prepares Cargo caches for CI by vendoring registry crates and fetching git dependencies
#
# This script:
# 1. Creates a CI-local CARGO_HOME
# 2. Vendors registry crates to vendor/
# 3. Generates .cargo-home/config.toml to use vendored sources
# 4. Fetches all dependencies (including git) to populate CARGO_HOME
#
# The cached artifacts (vendor/ + .cargo-home/) are platform-independent and can
# be shared across all OS/arch combinations in the build matrix.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NATIVE_CRATE="${REPO_ROOT}/src/native"
CARGO_HOME="${REPO_ROOT}/.cargo-home"
VENDOR_DIR="${REPO_ROOT}/vendor"

echo "=== Preparing Cargo cache ==="
echo "Repository root: ${REPO_ROOT}"
echo "Native crate: ${NATIVE_CRATE}"
echo "CARGO_HOME: ${CARGO_HOME}"
echo "Vendor directory: ${VENDOR_DIR}"

# Create CI-local CARGO_HOME
mkdir -p "${CARGO_HOME}"
export CARGO_HOME

# Navigate to native crate
cd "${NATIVE_CRATE}"

# Vendor registry crates and generate config
# This creates vendor/ and outputs config to stdout, which we redirect to CARGO_HOME/config.toml
echo "Vendoring registry crates..."
cargo vendor "${VENDOR_DIR}" > /tmp/vendor_output.txt 2>&1
# Extract the config section from the output (everything after "To use vendored sources...")
tail -n +$(grep -n "To use vendored sources" /tmp/vendor_output.txt | cut -d: -f1) /tmp/vendor_output.txt | tail -n +2 > "${CARGO_HOME}/config.toml"

echo ""
echo "Generated cargo config at: ${CARGO_HOME}/config.toml"
cat "${CARGO_HOME}/config.toml"
echo ""

# Fetch all dependencies (including git deps) to populate CARGO_HOME
# This populates:
# - .cargo-home/registry/cache (downloaded .crate files)
# - .cargo-home/registry/src (unpacked sources)
# - .cargo-home/git/db (bare git repositories)
# - .cargo-home/git/checkouts (checked-out git deps)
echo "Fetching all dependencies (including git)..."
cargo fetch --locked

echo ""
echo "=== Cargo cache preparation complete ==="
echo ""
echo "Cached directories:"
ls -lh "${VENDOR_DIR}" | head -5
echo "..."
echo ""
echo "CARGO_HOME contents:"
find "${CARGO_HOME}" -maxdepth 2 -type d
echo ""
echo "Cache is ready for upload!"
