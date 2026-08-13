#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
strategy_args=()
if [[ -n "${CI_ALLOCATION_STRATEGY:-}" ]]; then
    strategy_args=(--strategy "${CI_ALLOCATION_STRATEGY}")
fi

riot list --hash-only "${SUITE_NAME}" | sort | \
    ./scripts/ci_allocation_cli.py select \
        --suite "${SUITE_NAME}" \
        --node-index "${CI_NODE_INDEX:-1}" \
        --node-total "${CI_NODE_TOTAL:-1}" \
        "${strategy_args[@]}"
