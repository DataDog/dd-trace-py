#!/bin/bash
# The test name is the first argument,.  If no argument, error
if [ -z "$1" ]; then
  echo "Usage: $0 <test_name>"
  exit 1
fi
TEST=$1


DD_PROFILING_ENABLED=true \
DD_PROFILING_CRASHTRACKER_ENABLED=true \
DD_PROFILING_CRASHTRACKER_STDOUT_FILENAME=crashtracker.out.log \
DD_PROFILING_CRASHTRACKER_STDERR_FILENAME=crashtracker.err.log \
DD_PROFILING_CRASHTRACKER_STACKTRACE_RESOLVER=full \
DD_PROFILING_CRASHTRACKER_DEBUG_URL=http://localhost:8000 \
DD_SERVICE=my_crash_service_$TEST_NAME \
DD_ENV=crash_env \
DD_VERSION=crash_version \
pyenv exec ddtrace-run python tests/$TEST.py
