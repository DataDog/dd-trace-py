#!/usr/bin/env bash
set -e

DDTEST_CMD=scripts/ddtest

pkgs=$(python scripts/freshvenvs.py | cut -d':' -f1)
echo $pkgs

if ! $DDTEST_CMD; then
    echo "Command '$DDTEST_CMD' failed."
    exit 1
fi

for pkg in ${pkgs[*]}; do
    echo "Checking if new latest version exists for $pkg"
    export VENV_NAME="$pkg"
    RIOT_HASHES=( $(riot list --hash-only "^${VENV_NAME}$") )
    echo "Found ${#RIOT_HASHES[@]} riot hashes: ${RIOT_HASHES[@]}"
    if [[ ${#RIOT_HASHES[@]} -eq 0 ]]; then
        echo "No riot hashes found for pattern: $VENV_NAME"
    else
        echo "VENV_NAME=$VENV_NAME" >> $GITHUB_ENV
        for h in ${RIOT_HASHES[@]}; do 
            rm ".riot/requirements/${h}.txt"
        done
        scripts/compile-and-prune-test-requirements
        break
    fi
done