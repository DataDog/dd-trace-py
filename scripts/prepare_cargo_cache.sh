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
cargo vendor "${VENDOR_DIR}"

cat << EOF > ${CARGO_HOME}/config.toml
[source.crates-io]
replace-with = "vendored-sources"

[source."git+https://github.com/DataDog/libdatadog?rev=v24.0.0"]
git = "https://github.com/DataDog/libdatadog"
rev = "v24.0.0"
replace-with = "vendored-sources"

[source.vendored-sources]
directory = "${CI_PROJECT_DIR}/vendor"
EOF

echo "Generated cargo config at: ${CARGO_HOME}/config.toml"
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
echo "Set CARGO_HOME=\"${CARGO_HOME}\" to use the cache"
