#!/bin/bash
# Build script for FrameStack benchmark
# Requires: Google Benchmark library installed (libbenchmark-dev on Ubuntu/Debian)

set -e

echo "Building FrameStack benchmark..."
g++ -std=c++17 -O3 -DNDEBUG framestack_benchmark.cpp -lbenchmark -lpthread -o framestack_benchmark

echo "Build successful! Run with: ./framestack_benchmark"
echo ""
echo "Optional arguments:"
echo "  --benchmark_filter=<regex>     - Run only matching benchmarks"
echo "  --benchmark_repetitions=<N>    - Repeat each benchmark N times"
echo "  --benchmark_format=<console|json|csv> - Output format"
echo "  --benchmark_out=<filename>     - Write results to file"
echo ""
echo "Examples:"
echo "  ./framestack_benchmark --benchmark_filter=PushBack"
echo "  ./framestack_benchmark --benchmark_repetitions=5"
echo "  ./framestack_benchmark --benchmark_format=csv --benchmark_out=results.csv"

