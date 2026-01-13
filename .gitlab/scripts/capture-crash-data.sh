#!/usr/bin/env bash
# Analyze core dumps and crash data after test failure
set -eo pipefail

# In Docker containers, core dumps appear in the working directory
# because /proc/sys/kernel/core_pattern cannot be changed (read-only)
WORK_DIR="${CI_PROJECT_DIR:-.}"

echo "=== Checking for core dumps in ${WORK_DIR} ==="
echo "Current directory: $(pwd)"
echo "Kernel core pattern: $(cat /proc/sys/kernel/core_pattern 2>/dev/null || echo 'unknown')"
echo ""

# Look for core files matching patterns: core, core.*, core.<pid>
CORE_FILES=$(find "${WORK_DIR}" -maxdepth 1 -name "core*" -type f 2>/dev/null || true)

if [ -n "${CORE_FILES}" ]; then
    echo "=== Core dumps found ==="
    echo "${CORE_FILES}" | while read core; do
        echo "  - ${core}"
    done
    echo ""

    # Check if GDB is available
    if command -v gdb &> /dev/null; then
        echo "${CORE_FILES}" | while read core; do
            echo ""
            echo "========================================"
            echo "=== Analyzing: $(basename ${core}) ==="
            echo "========================================"

            # Get the actual Python binary (not pyenv shims)
            python_bin=$(python3 -c 'import sys; print(sys.executable)' 2>/dev/null || which python3 2>/dev/null || echo "python3")

            echo "Binary: ${python_bin}"
            echo "Core: ${core}"
            echo "Size: $(du -h ${core} | cut -f1)"
            echo ""

            # Run GDB to get backtrace
            gdb -q -batch \
                -ex "set pagination off" \
                -ex "thread apply all bt" \
                -ex "info threads" \
                -ex quit \
                "${python_bin}" "${core}" 2>&1 || echo "GDB analysis failed for ${core}"

            echo "========================================"
        done
    else
        echo "GDB not available (this should not happen in testrunner image)"
        echo "Core dump files:"
        ls -lh ${WORK_DIR}/core*
    fi
else
    echo "No core dumps found"
    echo ""
    echo "=== Debug info ==="
    echo "Ulimit -c: $(ulimit -c)"
    echo "Files in ${WORK_DIR}:"
    ls -la "${WORK_DIR}" | head -20
fi

echo ""
echo "=== End crash data capture ==="
