#!/usr/bin/env bash
# Run every finding repro with a timeout; summary at the end.
# Usage: ./run_all.sh [path-to-python]
#   default python: the editable-install venv on r1 (see INVESTIGATION.md)
PY=${1:-$HOME/dd/dd-trace-py-fi/.venv-fi/bin/python}
DIR="$(cd "$(dirname "$0")" && pwd)"
declare -A TIMEOUTS=(
  [repro_f1_run_path_exception_leak.py]=60
  [repro_f2_stop_path_exception_leak.py]=60
  [repro_f3_stop_wedge.py]=90
  [repro_f4_stack_snapshot_arm_miss.py]=60
  [repro_f5_lock_event_integrity.py]=60
  [repro_f6_telemetry_lifecycle.py]=60
  [repro_f7_fork_stderr.py]=60
  [repro_f8_registry_pollution.py]=60
)
declare -A RESULT
for f in repro_f*.py; do
  t=${TIMEOUTS[$f]:-60}
  echo
  echo "=== $f (timeout ${t}s) ==="
  timeout "$t" "$PY" "$DIR/$f"
  RESULT[$f]=$?
done
echo
echo "=== SUMMARY (1=reproduced, 0=not reproduced, 3=stop-wedge guard, 124=timeout) ==="
for f in "${!RESULT[@]}"; do printf '%-45s %s\n' "$f" "${RESULT[$f]}"; done | sort
