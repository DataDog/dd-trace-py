#!/usr/bin/env bash
#
# A/B benchmark: Lock profiler — pure Python (main) vs Cython branch.
#
# Uses interleaved rounds to eliminate ordering bias: each round runs
# one iteration per branch per capture rate, alternating main/cython.
# A warmup round (discarded) absorbs cold-start effects.
#
# Usage:
#   bash scripts/profiles/lock_profiler_cython/run.sh <cython-branch>
#
# Example:
#   bash scripts/profiles/lock_profiler_cython/run.sh vlad/lockprof-cythonize-hot-paths
#
# Environment:
#   LOCKBENCH_RUNS          Number of measured rounds per branch per rate (default: 7)
#   LOCKBENCH_OPS           Lock ops per workload run (default: 200000)
#   LOCKBENCH_THREADS       Threads for contended scenario (default: 4)
#   LOCKBENCH_CAPTURE_PCTS  Comma-separated capture rates to test (default: 1,50,100)

set -euo pipefail

CYTHON_BRANCH="${1:?Usage: $0 <cython-branch-name>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON="${PYTHON:-$(pyenv which python3 2>/dev/null || which python3)}"
RUNS="${LOCKBENCH_RUNS:-7}"
OPS="${LOCKBENCH_OPS:-200000}"
ORIGINAL_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
COLLECTOR_DIR="$REPO_ROOT/ddtrace/profiling/collector"

IFS=',' read -ra CAPTURE_PCTS <<< "${LOCKBENCH_CAPTURE_PCTS:-1,50,100}"

# ---------------------------------------------------------------------------
# CPU affinity and process priority (best-effort)
# ---------------------------------------------------------------------------
# On Linux, taskset pins the workload to a single core to reduce scheduling
# noise. On macOS (Darwin), there is no userland CPU affinity API, so we skip.
# nice -n -10 reduces scheduling interference on both platforms.
RUN_PREFIX=""
if command -v taskset &>/dev/null; then
    RUN_PREFIX="taskset -c 0"
fi
NICE_PREFIX="nice -n -10"

echo "=============================================="
echo "  Lock Profiler Cython A/B Benchmark"
echo "=============================================="
echo "Using Python: $PYTHON ($($PYTHON --version 2>&1))"
echo "Cython branch: $CYTHON_BRANCH"
echo "Measured rounds: $RUNS  (+ 1 warmup, discarded)"
echo "Ops per run: $OPS"
echo "Capture rates: ${CAPTURE_PCTS[*]}%"
echo "CPU affinity: ${RUN_PREFIX:-none (macOS)}"
echo "Original branch: $ORIGINAL_BRANCH"

# ---------------------------------------------------------------------------
# Venv setup
# ---------------------------------------------------------------------------
VENV_DIR="$REPO_ROOT/.riot/demo_lock_fix_venv"

if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo ""
    echo "--- Creating venv at $VENV_DIR ---"
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT" cython zstandard protobuf
    echo "Venv ready."
else
    echo "Reusing existing venv at $VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet cython 2>/dev/null || true
fi

PY="$VENV_DIR/bin/python"

# ---------------------------------------------------------------------------
# Results directory
# ---------------------------------------------------------------------------
RESULTS_DIR=$(mktemp -d)
trap 'echo ""; echo "Results saved in: $RESULTS_DIR"' EXIT

# Copy workload and analysis scripts to a temp directory so they survive
# git checkout between branches (the benchmark dir may not exist on main).
BENCH_TMP=$(mktemp -d)
cp "$SCRIPT_DIR/workload.py" "$BENCH_TMP/workload.py"
cp "$SCRIPT_DIR/analyze.py" "$BENCH_TMP/analyze.py"

# ---------------------------------------------------------------------------
# Helper: clean compiled .so files for _lock, _sampler, _task, _threading
# ---------------------------------------------------------------------------
clean_so() {
    rm -f "$COLLECTOR_DIR"/_lock.*.so "$COLLECTOR_DIR"/_sampler.*.so "$COLLECTOR_DIR"/_task.*.so
    rm -f "$COLLECTOR_DIR"/_lock.c "$COLLECTOR_DIR"/_sampler.c "$COLLECTOR_DIR"/_task.c
    rm -f "$REPO_ROOT/ddtrace/profiling"/_threading.*.so "$REPO_ROOT/ddtrace/profiling"/_threading.c
}

# ---------------------------------------------------------------------------
# Helper: build only the lock profiler Cython extensions (fast, ~2s)
# ---------------------------------------------------------------------------
build_cython() {
    echo "  Building Cython extensions..."
    "$PY" -c "
from Cython.Build import cythonize
from setuptools import Distribution, Extension

exts = cythonize([
    Extension('ddtrace.profiling.collector._sampler',
              ['ddtrace/profiling/collector/_sampler.pyx'], language='c'),
    Extension('ddtrace.profiling.collector._lock',
              ['ddtrace/profiling/collector/_lock.pyx'], language='c'),
    Extension('ddtrace.profiling.collector._task',
              ['ddtrace/profiling/collector/_task.pyx'], language='c'),
    Extension('ddtrace.profiling._threading',
              ['ddtrace/profiling/_threading.pyx'], language='c'),
])
dist = Distribution({'ext_modules': exts})
cmd = dist.get_command_obj('build_ext')
cmd.inplace = True
cmd.ensure_finalized()
cmd.run()
" 2>&1 | grep -v "^ld: warning" || true
    echo "  Done."
}

# ---------------------------------------------------------------------------
# Helper: run the workload once at a given capture rate
# ---------------------------------------------------------------------------
run_one() {
    local label="$1"
    local capture_pct="$2"
    local run_idx="$3"
    local out_dir="$RESULTS_DIR/pct_${capture_pct}/$label"
    local out_json="$out_dir/run_${run_idx}.json"
    local pprof_prefix="$out_dir/pprof_${run_idx}"

    mkdir -p "$out_dir"

    echo "    [$label] capture=${capture_pct}% run $run_idx/$RUNS ..."

    DD_PROFILING_ENABLED=1 \
    DD_PROFILING_LOCK_ENABLED=1 \
    DD_PROFILING_CAPTURE_PCT="$capture_pct" \
    DD_PROFILING_OUTPUT_PPROF="$pprof_prefix" \
    DD_PROFILING_UPLOAD_INTERVAL=1 \
    DD_TRACE_ENABLED=0 \
    LOCKBENCH_OPS="$OPS" \
    LOCKBENCH_THREADS="${LOCKBENCH_THREADS:-4}" \
        $NICE_PREFIX $RUN_PREFIX \
        "$PY" -m ddtrace.commands.ddtrace_run "$PY" "$BENCH_TMP/workload.py" \
        > "$out_json" 2>"$out_dir/run_${run_idx}.log"
}

# ---------------------------------------------------------------------------
# Helper: switch to a branch and prepare it for running
# ---------------------------------------------------------------------------
switch_to_main() {
    git -C "$REPO_ROOT" checkout main --quiet
    clean_so
}

switch_to_cython() {
    git -C "$REPO_ROOT" checkout "$CYTHON_BRANCH" --quiet
    clean_so
    build_cython
}

# ---------------------------------------------------------------------------
# Warmup round (results discarded)
#
# Absorbs import-time specialization, page faults, and filesystem cache
# priming so that measured rounds start from a warm steady state.
# ---------------------------------------------------------------------------
echo ""
echo "=== Warmup round (results discarded) ==="

echo "  --- main ---"
switch_to_main
for pct in "${CAPTURE_PCTS[@]}"; do
    WARMUP_DIR="$RESULTS_DIR/warmup/pct_${pct}/main"
    mkdir -p "$WARMUP_DIR"
    echo "    [main] warmup capture=${pct}% ..."
    DD_PROFILING_ENABLED=1 \
    DD_PROFILING_LOCK_ENABLED=1 \
    DD_PROFILING_CAPTURE_PCT="$pct" \
    DD_PROFILING_OUTPUT_PPROF="$WARMUP_DIR/pprof_0" \
    DD_PROFILING_UPLOAD_INTERVAL=1 \
    DD_TRACE_ENABLED=0 \
    LOCKBENCH_OPS="$OPS" \
    LOCKBENCH_THREADS="${LOCKBENCH_THREADS:-4}" \
        $NICE_PREFIX $RUN_PREFIX \
        "$PY" -m ddtrace.commands.ddtrace_run "$PY" "$BENCH_TMP/workload.py" \
        > "$WARMUP_DIR/run_0.json" 2>"$WARMUP_DIR/run_0.log"
done

echo "  --- cython ---"
switch_to_cython
for pct in "${CAPTURE_PCTS[@]}"; do
    WARMUP_DIR="$RESULTS_DIR/warmup/pct_${pct}/cython"
    mkdir -p "$WARMUP_DIR"
    echo "    [cython] warmup capture=${pct}% ..."
    DD_PROFILING_ENABLED=1 \
    DD_PROFILING_LOCK_ENABLED=1 \
    DD_PROFILING_CAPTURE_PCT="$pct" \
    DD_PROFILING_OUTPUT_PPROF="$WARMUP_DIR/pprof_0" \
    DD_PROFILING_UPLOAD_INTERVAL=1 \
    DD_TRACE_ENABLED=0 \
    LOCKBENCH_OPS="$OPS" \
    LOCKBENCH_THREADS="${LOCKBENCH_THREADS:-4}" \
        $NICE_PREFIX $RUN_PREFIX \
        "$PY" -m ddtrace.commands.ddtrace_run "$PY" "$BENCH_TMP/workload.py" \
        > "$WARMUP_DIR/run_0.json" 2>"$WARMUP_DIR/run_0.log"
done

# ---------------------------------------------------------------------------
# Measured rounds — interleaved
#
# Each round: checkout main → run once at every rate, then checkout
# cython → run once at every rate. This distributes both branches
# evenly across time, eliminating thermal drift, cache warmth, and
# background load bias.
# ---------------------------------------------------------------------------
echo ""
echo "=== Measured rounds (interleaved, $RUNS rounds) ==="

for round_idx in $(seq 1 "$RUNS"); do
    echo ""
    echo "--- Round $round_idx/$RUNS ---"

    echo "  [main]"
    switch_to_main
    for pct in "${CAPTURE_PCTS[@]}"; do
        run_one "main" "$pct" "$round_idx"
    done

    echo "  [cython]"
    switch_to_cython
    for pct in "${CAPTURE_PCTS[@]}"; do
        run_one "cython" "$pct" "$round_idx"
    done
done

# ---------------------------------------------------------------------------
# Restore original branch and rebuild if it's the Cython branch
# ---------------------------------------------------------------------------
git -C "$REPO_ROOT" checkout "$ORIGINAL_BRANCH" --quiet
if [ "$ORIGINAL_BRANCH" = "$CYTHON_BRANCH" ] || git -C "$REPO_ROOT" diff --name-only main.."$ORIGINAL_BRANCH" | grep -q '_lock.pyx'; then
    clean_so
    build_cython
fi
echo ""
echo "Restored branch: $ORIGINAL_BRANCH"

# ---------------------------------------------------------------------------
# Analyze
# ---------------------------------------------------------------------------
echo ""
echo "=== Results ==="
PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}" "$PY" "$BENCH_TMP/analyze.py" "$RESULTS_DIR"
