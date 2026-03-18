#!/usr/bin/env bash
#
# Microbenchmark: Thread info cache in lock profiler _flush_sample().
#
# Measures ns-level cost of thread metadata resolution paths:
# - get_ident baseline
# - Old: 2 separate lookups (get_thread_name + get_thread_native_id)
# - New: 1 combined lookup (get_thread_info)
# - Cached: int compare hit (zero dict lookups)
# - Realistic _flush_sample simulation (old vs cached)
# - Multi-thread worst case (100% cache misses)
#
# Usage:
#   bash scripts/profiles/lock_profiler/cache_thread_info/run.sh
#
# Requires: Cython-compiled _threading, _sampler, _lock extensions.
# Uses the same venv as other lock profiler benchmarks (.riot/demo_lock_fix_venv).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON="${PYTHON:-$(pyenv which python3 2>/dev/null || which python3)}"
COLLECTOR_DIR="$REPO_ROOT/ddtrace/profiling/collector"
THREADING_DIR="$REPO_ROOT/ddtrace/profiling"

VENV_DIR="$REPO_ROOT/.riot/demo_lock_fix_venv"

RUN_PREFIX=""
if command -v taskset &>/dev/null; then
    RUN_PREFIX="taskset -c 0"
fi
NICE_PREFIX="nice -n -10"

echo "=============================================="
echo "  Lock Profiler Thread Info Cache Microbenchmark"
echo "=============================================="

# ---------------------------------------------------------------------------
# Venv setup
# ---------------------------------------------------------------------------
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
echo "Using Python: $PY ($($PY --version 2>&1))"
echo "CPU affinity: ${RUN_PREFIX:-none (macOS)}"
echo ""

# ---------------------------------------------------------------------------
# Build Cython extensions
# ---------------------------------------------------------------------------
echo "--- Building Cython extensions ---"
rm -f "$COLLECTOR_DIR"/_lock.*.so "$COLLECTOR_DIR"/_lock.c
rm -f "$COLLECTOR_DIR"/_sampler.*.so "$COLLECTOR_DIR"/_sampler.c
rm -f "$THREADING_DIR"/_threading.*.so "$THREADING_DIR"/_threading.c

"$PY" -c "
from Cython.Build import cythonize
from setuptools import Distribution, Extension

exts = cythonize([
    Extension('ddtrace.profiling._threading',
              ['ddtrace/profiling/_threading.pyx'], language='c'),
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
echo "Done."
echo ""

# ---------------------------------------------------------------------------
# Run benchmark
#
# Summary goes to stderr, JSON to stdout.
# Usage: bash run.sh [--matrix]
#   --matrix  Run 4-scenario matrix (no changes, cache only, .get() only, cache+.get())
# ---------------------------------------------------------------------------
echo "--- Running microbenchmark ---"
DD_PROFILING_ENABLED=1 \
DD_PROFILING_LOCK_ENABLED=1 \
DD_PROFILING_CAPTURE_PCT=100 \
DD_TRACE_ENABLED=0 \
    $NICE_PREFIX $RUN_PREFIX \
    "$PY" -m ddtrace.commands.ddtrace_run "$PY" "$SCRIPT_DIR/benchmark_thread_cache.py" "$@"
