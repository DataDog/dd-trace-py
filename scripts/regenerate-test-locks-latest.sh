#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
    echo "Usage: $0 [suite]"
    exit 1
fi

if [[ $# -eq 1 ]]; then
    suites="$1"
else
    suites=$(scripts/freshvenvs.py output)
fi

echo "Outdated suites: $suites"
if [[ -z "$suites" ]]; then
    echo "No outdated suites found."
    exit 0
fi

for suite in $suites; do
    export VENV_NAME="$suite"
    if [[ -n "${GITHUB_ENV:-}" ]]; then
        echo "VENV_NAME=$VENV_NAME" >> "$GITHUB_ENV"
    fi
    if ! scripts/test-env list "$suite" >/dev/null 2>&1; then
        echo "No test environments found for $suite"
        continue
    fi

    scripts/test-env lock "$suite"
    mapfile -t regenerated_locks < <(git diff --name-only -- .uv)
    if [[ ${#regenerated_locks[@]} -gt 0 ]]; then
        python scripts/check_lockfile_cooldown.py "${regenerated_locks[@]}"
    fi
    break
done
