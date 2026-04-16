#!/usr/bin/env bash
set -euo pipefail

# Build the Docker image for profiler native allocation analysis.
# Run from the repo root:
#   scripts/profiler-allocs/build.sh

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
IMAGE_NAME="ddtrace-profiler-allocs"

cd "${REPO_ROOT}"

echo "==> Building ${IMAGE_NAME} Docker image..."
echo "    (this builds dd-trace-py with native profiling extensions)"

docker build \
    -t "${IMAGE_NAME}" \
    -f scripts/profiler-allocs/Dockerfile \
    .

echo ""
echo "==> Build complete: ${IMAGE_NAME}"
echo ""
echo "Run with:"
echo "  mkdir -p artifacts"
echo "  docker run --rm -v \$(pwd)/artifacts:/artifacts ${IMAGE_NAME}"
echo ""
echo "Or with custom duration:"
echo "  docker run --rm -e TEST_DURATION=60 -v \$(pwd)/artifacts:/artifacts ${IMAGE_NAME}"
