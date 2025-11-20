#!/usr/bin/env bash
set -exo pipefail

ARTIFACTS_DIR="${1}"

files=($ARTIFACTS_DIR/results*.json)

# Pop the last one off the array
last="${files[-1]}"
unset 'files[-1]'

cmd=(/app/benchmarks/.venv_candidate/bin/pyperf convert --stdout)

for f in "${files[@]}"; do
    cmd+=(--add "$f")
done

cmd+=("$last")

printf '%q ' "${cmd[@]}"
"${cmd[@]}" > "${ARTIFACTS_DIR}/results.json"

cat "${ARTIFACTS_DIR}/results.json"
