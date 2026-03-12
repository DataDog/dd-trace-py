#!/usr/bin/env bash
#
# A/B benchmark: Lock profiler — pure Python (main) vs Cython branch.
#
# Checks out main and the Cython branch, rebuilds the lock profiler
# Cython extensions after each checkout, runs the workload N times
# per branch at multiple capture rates, then compares results.
#
# Usage:
#   bash scripts/profiles/lock_profiler_cython/run.sh <cython-branch>
#
# Example:
#   bash scripts/profiles/lock_profiler_cython/run.sh vlad/lockprof-cythonize-hot-paths
#
# Environment:
#   LOCKBENCH_RUNS          Number of iterations per branch per rate (default: 5)
#   LOCKBENCH_OPS           Lock ops per workload run (default: 50000)
#   LOCKBENCH_THREADS       Threads for contended scenario (default: 4)
#   LOCKBENCH_CAPTURE_PCTS  Comma-separated capture rates to test (default: 1,50,100)

set -euo pipefail

CYTHON_BRANCH="${1:?Usage: $0 <cython-branch-name>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON="${PYTHON:-$(pyenv which python3 2>/dev/null || which python3)}"
RUNS="${LOCKBENCH_RUNS:-5}"
ORIGINAL_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
COLLECTOR_DIR="$REPO_ROOT/ddtrace/profiling/collector"

IFS=',' read -ra CAPTURE_PCTS <<< "${LOCKBENCH_CAPTURE_PCTS:-1,50,100}"

echo "=============================================="
echo "  Lock Profiler Cython A/B Benchmark"
echo "=============================================="
echo "Using Python: $PYTHON ($($PYTHON --version 2>&1))"
echo "Cython branch: $CYTHON_BRANCH"
echo "Runs per branch per rate: $RUNS"
echo "Capture rates: ${CAPTURE_PCTS[*]}%"
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

# ---------------------------------------------------------------------------
# Helper: clean compiled .so files for _lock and _sampler
# ---------------------------------------------------------------------------
clean_so() {
    rm -f "$COLLECTOR_DIR"/_lock.*.so "$COLLECTOR_DIR"/_sampler.*.so
    rm -f "$COLLECTOR_DIR"/_lock.c "$COLLECTOR_DIR"/_sampler.c
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
    LOCKBENCH_OPS="${LOCKBENCH_OPS:-50000}" \
    LOCKBENCH_THREADS="${LOCKBENCH_THREADS:-4}" \
        "$PY" -m ddtrace.commands.ddtrace_run "$PY" "$SCRIPT_DIR/workload.py" \
        > "$out_json" 2>"$out_dir/run_${run_idx}.log"
}

# ---------------------------------------------------------------------------
# Phase 1: main (pure Python _lock.py)
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 1: main (pure Python) ==="
git -C "$REPO_ROOT" checkout main --quiet
clean_so

for pct in "${CAPTURE_PCTS[@]}"; do
    echo "  --- capture_pct=${pct}% ---"
    for i in $(seq 1 "$RUNS"); do
        run_one "main" "$pct" "$i"
    done
done

# ---------------------------------------------------------------------------
# Phase 2: Cython branch (_lock.pyx compiled to .so)
# ---------------------------------------------------------------------------
echo ""
echo "=== Phase 2: $CYTHON_BRANCH (Cython) ==="
git -C "$REPO_ROOT" checkout "$CYTHON_BRANCH" --quiet
clean_so
build_cython

for pct in "${CAPTURE_PCTS[@]}"; do
    echo "  --- capture_pct=${pct}% ---"
    for i in $(seq 1 "$RUNS"); do
        run_one "cython" "$pct" "$i"
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
PYTHONPATH="$REPO_ROOT:${PYTHONPATH:-}" "$PY" "$SCRIPT_DIR/analyze.py" "$RESULTS_DIR"
