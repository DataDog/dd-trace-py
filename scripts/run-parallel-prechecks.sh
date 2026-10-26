#!/usr/bin/env bash
# Run the CI prechecks in parallel.
#
# Input is a check list where each non-comment line is "name<TAB>command".
# Every check runs in its own background process with its output captured to
# a log file. Once all checks have finished, the logs are printed in the
# original list order as collapsible GitLab sections (failed checks are left
# expanded), followed by a summary table. All checks always run to completion
# before exiting, so one failure does not hide the others; the script exits
# non-zero if any check failed.
#
# Usage:
#   scripts/run-parallel-prechecks.sh [CHECKS_FILE]
#
# CHECKS_FILE defaults to "-" (read the check list from stdin).

set -uo pipefail

if [[ $# -gt 1 ]]; then
  echo "usage: $0 [CHECKS_FILE]" >&2
  exit 2
fi

CHECKS_FILE="${1:--}"
if [[ "$CHECKS_FILE" != "-" ]]; then
  if [[ ! -f "$CHECKS_FILE" ]]; then
    echo "$0: checks file not found: $CHECKS_FILE" >&2
    exit 2
  fi
  exec 0< "$CHECKS_FILE" || exit 2
fi

LOG_DIR="$(mktemp -d)"
trap 'rm -rf "$LOG_DIR"' EXIT

NAMES=()
COMMANDS=()
PIDS=()

# Run one check in the background, capturing output to <idx>.log and recording
# the exit code and wall time in <idx>.status for the parent to report later.
run_one() {
  local idx="$1" cmd="$2"
  local log="$LOG_DIR/$idx.log"
  local start end rc
  start=$(date +%s)
  bash -c "$cmd" > "$log" 2>&1
  rc=$?
  end=$(date +%s)
  printf '%s %s\n' "$rc" "$((end - start))" > "$LOG_DIR/$idx.status"
  return "$rc"
}

idx=0
while IFS=$'\t' read -r name cmd; do
  if [[ -z "$name" || "${name:0:1}" == "#" ]]; then
    continue
  fi
  if [[ -z "$cmd" ]]; then
    echo "$0: malformed check line (expected name<TAB>command): $name" >&2
    exit 2
  fi
  NAMES+=("$name")
  COMMANDS+=("$cmd")
  run_one "$idx" "$cmd" &
  PIDS+=("$!")
  idx=$((idx + 1))
done

FAILED=0
if [[ ${#PIDS[@]} -gt 0 ]]; then
  for i in "${!PIDS[@]}"; do
    if ! wait "${PIDS[$i]}"; then
      FAILED=1
    fi
  done
fi

if [[ ${#NAMES[@]} -gt 0 ]]; then
  for i in "${!NAMES[@]}"; do
    status="$(cat "$LOG_DIR/$i.status")"
    rc="${status%% *}"
    duration="${status##* }"
    if [[ "$rc" -eq 0 ]]; then
      result="PASS"
      collapsed="[collapsed=true]"
    else
      result="FAIL"
      collapsed=""
    fi
    echo -e "\e[0Ksection_start:$(date +%s):check_${i}${collapsed}\r\e[0K${NAMES[$i]}: ${result} in ${duration}s"
    cat "$LOG_DIR/$i.log"
    echo -e "\e[0Ksection_end:$(date +%s):check_${i}\r\e[0K"
  done

  echo ""
  echo "Precheck summary:"
  for i in "${!NAMES[@]}"; do
    status="$(cat "$LOG_DIR/$i.status")"
    rc="${status%% *}"
    duration="${status##* }"
    if [[ "$rc" -eq 0 ]]; then
      result="PASS"
    else
      result="FAIL"
    fi
    printf '  %-4s %4ss  %s\n' "$result" "$duration" "${NAMES[$i]}"
  done
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo "One or more prechecks failed." >&2
fi
exit "$FAILED"
