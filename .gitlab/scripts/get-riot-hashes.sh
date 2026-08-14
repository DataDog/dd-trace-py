#!/usr/bin/env bash
set -e -u -o pipefail

SUITE_NAME="${1:-}"
CI_ALLOCATION_SUITE="${2:-${CI_ALLOCATION_SUITE:-${SUITE_NAME}}}"
strategy_args=()
if [[ -n "${CI_ALLOCATION_STRATEGY:-}" ]]; then
    strategy_args=(--strategy "${CI_ALLOCATION_STRATEGY}")
fi

mapfile -t available_hashes < <(riot list --hash-only "${SUITE_NAME}" | sort)
if [[ -n "${CI_ALLOCATION_ASSIGNMENTS:-}" ]]; then
    IFS=';' read -r -a planned_assignments <<< "${CI_ALLOCATION_ASSIGNMENTS}"
    node_index="${CI_NODE_INDEX:-1}"
    node_total="${CI_NODE_TOTAL:-1}"
    if [[ ! "${node_index}" =~ ^[1-9][0-9]*$ || ! "${node_total}" =~ ^[1-9][0-9]*$ ]]; then
        echo "CI node index and total must be positive integers" >&2
        exit 1
    fi
    if [[ "${#planned_assignments[@]}" -ne "${node_total}" ]]; then
        echo "Generated allocation count differs from CI_NODE_TOTAL" >&2
        exit 1
    fi
    if [[ "${node_index}" -gt "${#planned_assignments[@]}" ]]; then
        echo "CI_NODE_INDEX is outside the generated allocation" >&2
        exit 1
    fi
    CI_ALLOCATION_UNITS="${planned_assignments[$((node_index - 1))]}"
fi
if [[ -n "${CI_ALLOCATION_UNITS:-}" ]]; then
    declare -A available=()
    for riot_hash in "${available_hashes[@]}"; do
        available["${riot_hash}"]=1
    done
    IFS=',' read -r -a execution_units <<< "${CI_ALLOCATION_UNITS}"
    for unit in "${execution_units[@]}"; do
        if [[ ! "${unit}" =~ ^([0-9a-f]+)(@([1-9][0-9]*)/([1-9][0-9]*))?$ ]]; then
            echo "Invalid Riot execution unit: ${unit}" >&2
            exit 1
        fi
        riot_hash="${BASH_REMATCH[1]}"
        if [[ -z "${available[${riot_hash}]:-}" ]]; then
            echo "Generated Riot execution unit is not in ${SUITE_NAME}: ${unit}" >&2
            exit 1
        fi
        printf '%s\n' "${unit}"
    done
    exit 0
fi

printf '%s\n' "${available_hashes[@]}" | \
    ./scripts/ci_allocation_cli.py select \
        --suite "${CI_ALLOCATION_SUITE}" \
        --node-index "${CI_NODE_INDEX:-1}" \
        --node-total "${CI_NODE_TOTAL:-1}" \
        "${strategy_args[@]}"
