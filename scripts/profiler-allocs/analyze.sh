#!/usr/bin/env bash
set -euo pipefail

# Analyze native allocation pprof files captured by ddprof.
# Focuses on allocation COUNT (fragmentation impact) from the dd-trace-py
# profiler's own code, excluding application workload allocations.
#
# Usage:
#   scripts/profiler-allocs/analyze.sh [artifacts_dir]

ARTIFACTS_DIR="${1:-$(pwd)/artifacts}"

# Profiler code paths to focus on
PROFILER_FOCUS="dd_wrapper|ddup|memalloc|_stack|libdatadog|libdd|sample|profil|echion|_native|uploader|Datadog::|ChainAllocator|SliceSet|intern_string"
# Application workload paths to exclude (they go through _memalloc hooks but
# are not the profiler's own allocations)
APP_IGNORE="bytearray|PyByteArray|PyList_Append|_PyList_Append"

if ! command -v go &>/dev/null; then
    echo "Error: go is not installed (needed for 'go tool pprof')."
    exit 1
fi

# Decompress .pprof.zst files if zstd is available
if command -v zstd &>/dev/null; then
    for f in "${ARTIFACTS_DIR}"/*.pprof.zst; do
        [ -f "$f" ] || continue
        out="${f%.zst}"
        if [ ! -f "$out" ] || [ "$f" -nt "$out" ]; then
            zstd -d "$f" -o "$out" -f --quiet
        fi
    done
else
    echo "Warning: zstd not found. Cannot decompress .pprof.zst files."
    echo "Install with: brew install zstd"
fi

# Collect all plain .pprof files
pprof_files=()
while IFS= read -r -d '' f; do
    pprof_files+=("$f")
done < <(find "${ARTIFACTS_DIR}" -name "*.pprof" ! -name "*.pprof.zst" -print0 2>/dev/null | sort -z)

if [ ${#pprof_files[@]} -eq 0 ]; then
    echo "No .pprof files found in ${ARTIFACTS_DIR}"
    echo ""
    echo "Run the Docker container first:"
    echo "  mkdir -p artifacts"
    echo "  docker run --rm --privileged -v \$(pwd)/artifacts:/artifacts ddtrace-profiler-allocs"
    exit 1
fi

echo "========================================================================"
echo "  Native Allocation Analysis — dd-trace-py profiler"
echo "  Focus: allocation COUNT (fragmentation impact)"
echo "========================================================================"
echo ""
echo "Found ${#pprof_files[@]} pprof file(s) in ${ARTIFACTS_DIR}"
echo ""

# --- Section 1: Allocation count per profile (profiler only) ---
echo "------------------------------------------------------------------------"
echo "  1. Profiler allocation count over time (alloc-samples)"
echo "     Excludes application workload (bytearray/list)"
echo "------------------------------------------------------------------------"
echo ""
for f in "${pprof_files[@]}"; do
    echo "--- $(basename "$f") ---"
    go tool pprof -top -sample_index=alloc-samples -nodecount=20 \
        -focus="${PROFILER_FOCUS}" \
        -ignore="${APP_IGNORE}" \
        "$f" 2>/dev/null | grep -E "(flat|Total|Duration|Active|Showing|^\s+[0-9])" || true
    echo ""
done

# Use the last profile for steady-state deep dives
if [ ${#pprof_files[@]} -gt 1 ]; then
    steady_state="${pprof_files[-1]}"
    echo "(Using last profile for steady-state analysis below)"
else
    steady_state="${pprof_files[0]}"
fi
echo ""

# --- Section 2: Cumulative call chains driving allocations ---
echo "------------------------------------------------------------------------"
echo "  2. Top cumulative call chains (alloc-samples, steady state)"
echo "     Shows which profiler operations drive the most allocations"
echo "------------------------------------------------------------------------"
echo ""
go tool pprof -top -sample_index=alloc-samples -nodecount=30 -cum \
    -focus="${PROFILER_FOCUS}" \
    -ignore="${APP_IGNORE}" \
    "${steady_state}" 2>/dev/null || true
echo ""

# --- Section 3: Live heap (what's still held) ---
echo "------------------------------------------------------------------------"
echo "  3. Profiler live heap (inuse-space) — retained allocations"
echo "------------------------------------------------------------------------"
echo ""
go tool pprof -top -sample_index=inuse-space -nodecount=25 \
    -focus="${PROFILER_FOCUS}" \
    "${steady_state}" 2>/dev/null || true
echo ""

# --- Section 4: Live object count ---
echo "------------------------------------------------------------------------"
echo "  4. Profiler live objects (inuse-objects)"
echo "------------------------------------------------------------------------"
echo ""
go tool pprof -top -sample_index=inuse-objects -nodecount=20 \
    -focus="${PROFILER_FOCUS}" \
    "${steady_state}" 2>/dev/null || true
echo ""

# --- Interactive ---
echo "========================================================================"
echo "  Interactive exploration"
echo "========================================================================"
echo ""
echo "Allocation count flamegraph (profiler only, excludes app workload):"
echo "  go tool pprof -http=:8080 -sample_index=alloc-samples -focus='${PROFILER_FOCUS}' -ignore='${APP_IGNORE}' ${steady_state}"
echo ""
echo "Live heap flamegraph (profiler code):"
echo "  go tool pprof -http=:8080 -sample_index=inuse-space -focus='${PROFILER_FOCUS}' ${steady_state}"
echo ""
echo "All profiles:"
for f in "${pprof_files[@]}"; do
    echo "  ${f}"
done
echo ""
