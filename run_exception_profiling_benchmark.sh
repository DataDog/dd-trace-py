#!/bin/bash

# Exception profiling benchmark runner
# Tests different sampling intervals and baseline performance

echo "========================================="
echo "Exception Profiling Benchmark"
echo "========================================="
echo ""

# Baseline - profiling enabled but exception profiling disabled
echo "Profiling Enabled, Exception Profiling Disabled:"
echo "-----------------------------------------"
DD_PROFILING_ENABLED=true DD_PROFILING_EXCEPTION_ENABLED=false python benchmark_exception_profiling.py
echo ""

# Test different sampling intervals
SAMPLING_INTERVALS=(10 100 1000 10000)

for interval in "${SAMPLING_INTERVALS[@]}"; do
    echo "Exception Profiling - Sampling Interval: $interval"
    echo "-----------------------------------------"
    DD_PROFILING_ENABLED=true DD_PROFILING_EXCEPTION_ENABLED=true DD_PROFILING_EXCEPTION_SAMPLING_INTERVAL=$interval python benchmark_exception_profiling.py
    echo ""
done

echo "========================================="
echo "Benchmark Complete"
echo "========================================="
