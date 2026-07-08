#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 <package>"
    exit 1
fi

PACKAGE_NAME="$1"

echo "Checking if new latest version exists for $PACKAGE_NAME"

if ! RIOT_HASHES_OUTPUT=$(riot list --hash-only "^${PACKAGE_NAME}$" 2>&1); then
    echo "Error running riot list for $PACKAGE_NAME: $RIOT_HASHES_OUTPUT"
    exit 1
fi
mapfile -t RIOT_HASHES <<< "$RIOT_HASHES_OUTPUT"
RIOT_HASHES=("${RIOT_HASHES[@]//[[:space:]]/}")
RIOT_HASHES=(${RIOT_HASHES[@]})

echo "Found ${#RIOT_HASHES[@]} riot hashes: ${RIOT_HASHES[*]}"

if [[ ${#RIOT_HASHES[@]} -eq 0 ]]; then
    echo "No riot hashes found for pattern: $PACKAGE_NAME"
    exit 1
fi

for h in "${RIOT_HASHES[@]}"; do
    echo "Removing riot lockfile: .riot/requirements/${h}.txt"
    rm -f ".riot/requirements/${h}.txt"
done

scripts/compile-and-prune-test-requirements

# Supply-chain hardening (TEST-CD, APMLP-1362): verify that none of the
# newly resolved pins (including transitive dependencies that pip-tools picks
# up) are younger than the cooldown.
REGENERATED_LOCKFILES=()
for h in "${RIOT_HASHES[@]}"; do
    if [[ -f ".riot/requirements/${h}.txt" ]]; then
        REGENERATED_LOCKFILES+=(".riot/requirements/${h}.txt")
    fi
done

if [[ ${#REGENERATED_LOCKFILES[@]} -gt 0 ]]; then
    echo "Validating cooldown on ${#REGENERATED_LOCKFILES[@]} regenerated lockfile(s)"
    python scripts/check_lockfile_cooldown.py "${REGENERATED_LOCKFILES[@]}"
fi
