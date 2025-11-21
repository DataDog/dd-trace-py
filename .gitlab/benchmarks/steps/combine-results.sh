#!/usr/bin/env bash
set -exo pipefail

ARTIFACTS_DIR="${1}"

jq -s '
  map(
    . as $file
    | .benchmarks |= map(
        .metadata = ($file.metadata | { name, loops, cpu_affinity, cpu_config, cpu_freq } )
      )
    | {
        benchmarks: .benchmarks,
        leftover_meta: (.metadata | del(.name, .loops, .cpu_affinity, .cpu_config, .cpu_freq))
      }
  )
  |
  {
    benchmarks: (map(.benchmarks) | add),
    metadata:   (first | .leftover_meta)
  }
' $ARTIFACTS_DIR/results.*.json > "${ARTIFACTS_DIR}/results.json"
