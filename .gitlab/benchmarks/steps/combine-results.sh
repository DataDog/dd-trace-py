#!/usr/bin/env bash
set -exo pipefail

ARTIFACTS_DIR="${1}"

# Combine all the individual results into a single results fule.
# We need:
#   - to merge all the benchmarks into a single list
#   - to keep only one copy of the metadata, removing fields that are per-benchmark specific
#   - add benchmark specific metadata into each benchmark entry
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
