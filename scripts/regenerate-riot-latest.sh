#!/usr/bin/env bash
set -euo pipefail

DDTEST_CMD=scripts/ddtest

pkgs=$(python scripts/freshvenvs.py output)
echo "Outdated packages: $pkgs"

if [[ -z "$pkgs" ]]; then
    echo "No outdated packages found."
    exit 0
fi

if ! "$DDTEST_CMD"; then
    echo "Command '$DDTEST_CMD' failed."
    exit 1
fi

for pkg in $pkgs; do
    echo "Checking if new latest version exists for $pkg"
    export VENV_NAME="$pkg"

    if ! RIOT_HASHES_OUTPUT=$(riot list --hash-only "^${VENV_NAME}$" 2>&1); then
        echo "Error running riot list for $pkg: $RIOT_HASHES_OUTPUT"
        continue
    fi
    mapfile -t RIOT_HASHES <<< "$RIOT_HASHES_OUTPUT"
    RIOT_HASHES=("${RIOT_HASHES[@]//[[:space:]]/}")
    RIOT_HASHES=(${RIOT_HASHES[@]})

    echo "Found ${#RIOT_HASHES[@]} riot hashes: ${RIOT_HASHES[*]}"

    if [[ ${#RIOT_HASHES[@]} -eq 0 ]]; then
        echo "No riot hashes found for pattern: $VENV_NAME"
        continue
    fi

    if [[ -n "${GITHUB_ENV:-}" ]]; then
        echo "VENV_NAME=$VENV_NAME" >> "$GITHUB_ENV"
    fi

    for h in "${RIOT_HASHES[@]}"; do
        echo "Removing riot lockfile: .riot/requirements/${h}.txt"
        rm -f ".riot/requirements/${h}.txt"
    done

    scripts/compile-and-prune-test-requirements

    # Supply-chain hardening (TEST-CD, APMLP-1362): verify that none of the
    # newly resolved pins (including transitive dependencies that pip-tools
    # picks up) are younger than the cooldown. This is defense-in-depth on
    # top of the cooldown applied in freshvenvs.py, since pip-tools has no
    # --exclude-newer flag of its own.
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

    # Only process one package per run
    break
done
