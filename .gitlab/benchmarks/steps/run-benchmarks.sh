#!/usr/bin/env bash
set -ex -o pipefail

# Resolve the script directory now, before the cd below makes the
# invocation-relative path unresolvable.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

export ARTIFACTS_DIR="/artifacts/${CI_JOB_ID}-${SCENARIO}"
mkdir -p "${ARTIFACTS_DIR}"
mkdir -p "${ARTIFACTS_DIR}/baseline"
mkdir -p "${ARTIFACTS_DIR}/candidate"

# Setup benchmark scenario code
mkdir -p "/app/benchmarks"
cp -a "${CI_PROJECT_DIR:-.}/benchmarks/base/." "/app/benchmarks/"
cp -a "${CI_PROJECT_DIR:-.}/benchmarks/bm" "/app/benchmarks/"
cp -a "${CI_PROJECT_DIR:-.}/benchmarks/$SCENARIO/." "/app/benchmarks/"
cd /app/benchmarks

# Run candidate benchmarks
export CANDIDATE_WHL="${CI_PROJECT_DIR}/${CANDIDATE_WHL}"
if [ ! -f "$CANDIDATE_WHL" ]; then
  echo "Candidate wheel not found: $CANDIDATE_WHL"
  exit 1
fi

# Assign 10 CPUs to the candidate
# CPU cores 0-23 are used for Gitlab / OS-specific things and are on CPU socket 0,
# cores 24-47 are for benchmarks and are on separate CPU socket 1.
# CPU 24 is skipped because it is noisier than the other cores, and CPU 25 is skipped so the
# candidate and baseline each get the same number of CPUs.
CPU_AFFINITY=26-35 "$SCRIPT_DIR/run-candidate.sh" &

# Run baseline benchmarks
export BASELINE_WHL="${CI_PROJECT_DIR}/${BASELINE_WHL}"
if [ -f "$BASELINE_WHL" ]; then

  # Assign 10 CPUs to the baseline
  # CPU cores 0-23 are used for Gitlab / OS-specific things and are on CPU socket 0,
  # cores 24-47 are for benchmarks and are on separate CPU socket 1.
  # CPUs 36-37 are skipped to match the candidate's CPU count.
  CPU_AFFINITY=38-47 "$SCRIPT_DIR/run-baseline.sh" &

  # baseline_branch.txt is used by big regression check
  # TODO: Deprecate after we switch to new way of running benchmarks in other lang repos
  echo "baseline" > "$REPORTS_DIR"/baseline_branch.txt
else
  echo "Baseline wheel not found: $BASELINE_WHL"
fi

# Wait for all tasks to finish
pids=$(jobs -p)
echo "Waiting for all tasks to finish: $pids"
wait $pids
echo "All tasks finished"
