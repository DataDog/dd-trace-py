#!/usr/bin/env bash
#
# Demo: Lock profiler module-cloning fix
#
# Creates a venv, installs ddtrace, starts the Datadog agent, runs the lock
# workload under ddtrace-run on main and the fix branch, then compares.
#
# Usage:
#   bash scripts/profiles/lock_profiler_fix/run.sh <fix-branch>
#
# Example:
#   bash scripts/profiles/lock_profiler_fix/run.sh vlad/lockprof-fix-threading-mod-cloning-bug

set -euo pipefail

FIX_BRANCH="${1:?Usage: $0 <fix-branch-name>}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
PYTHON="${PYTHON:-$(pyenv which python3 2>/dev/null || which python3)}"

echo "=============================================="
echo "  Lock Profiler Module-Cloning Fix Demo"
echo "=============================================="
echo "Using Python: $PYTHON ($($PYTHON --version 2>&1))"

# ---------------------------------------------------------------------------
# Create a venv with ddtrace installed (editable)
# ---------------------------------------------------------------------------
VENV_DIR="$REPO_ROOT/.riot/demo_lock_fix_venv"

if [ ! -f "$VENV_DIR/bin/python" ]; then
    echo ""
    echo "--- Creating venv at $VENV_DIR ---"
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT" zstandard protobuf
    echo "Venv ready."
else
    echo "Reusing existing venv at $VENV_DIR"
fi

PY="$VENV_DIR/bin/python"

# Verify
"$PY" -c "import ddtrace; import zstandard" 2>/dev/null || {
    echo "ERROR: venv broken, removing and re-creating..."
    rm -rf "$VENV_DIR"
    "$PYTHON" -m venv "$VENV_DIR"
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet -e "$REPO_ROOT" zstandard protobuf
}

# ---------------------------------------------------------------------------
# Start the Datadog agent
# ---------------------------------------------------------------------------
echo ""
echo "--- Starting Datadog agent ---"
docker compose -f "$REPO_ROOT/docker-compose.yml" up -d ddagent
echo -n "Waiting for agent "
for i in $(seq 1 30); do
    if curl -sf http://localhost:8126/info > /dev/null 2>&1; then
        echo " ready."
        break
    fi
    echo -n "."
    sleep 1
    [ "$i" -eq 30 ] && echo " WARNING: agent not ready, continuing."
done

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"; docker compose -f "$REPO_ROOT/docker-compose.yml" stop ddagent 2>/dev/null' EXIT

# ---------------------------------------------------------------------------
# Helper: run the workload on the current branch
# ---------------------------------------------------------------------------
run_workload() {
    local label="$1"
    local pprof_prefix="$TMPDIR/$label"

    echo ""
    echo "--- [$label] Running lock workload ---"

    local pid
    pid=$(
        DD_PROFILING_ENABLED=1 \
        DD_PROFILING_LOCK_ENABLED=1 \
        DD_PROFILING_CAPTURE_PCT=100 \
        DD_PROFILING_OUTPUT_PPROF="$pprof_prefix" \
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE=1 \
        DD_TRACE_AGENT_URL=http://localhost:8126 \
        DD_SERVICE=lock-profiler-demo \
        DD_ENV=demo \
            "$PY" -m ddtrace.commands.ddtrace_run "$PY" "$SCRIPT_DIR/workload.py"
    )

    echo "  PID: $pid"
    echo "$pid" > "$TMPDIR/${label}.pid"
}

# ---------------------------------------------------------------------------
# Run on main, then on fix branch
# ---------------------------------------------------------------------------
git checkout main
run_workload "main"

git checkout "$FIX_BRANCH"
run_workload "fix"

# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------
echo ""
echo "--- Comparing results ---"
"$PY" "$SCRIPT_DIR/analyze.py" \
    "$TMPDIR/main" "$(cat "$TMPDIR/main.pid")" \
    "$TMPDIR/fix"  "$(cat "$TMPDIR/fix.pid")"
