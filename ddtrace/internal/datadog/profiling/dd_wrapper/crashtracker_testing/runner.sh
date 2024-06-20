#!/bin/bash
# The test name is the first argument,.  If no argument, error
if [ -z "$1" ]; then
  echo "Usage: $0 <test_name>"
  exit 1
fi
TEST=$1
OUTFILE=crashtracker.out.log
ERRFILE=crashtracker.err.log

rm -f $OUTFILE $ERRFILE

#DD_PROFILING_CRASHTRACKER_DEBUG_URL=http://localhost:8000 \
DD_PROFILING_ENABLED=true \
DD_TRACE_LOGGING_RATE=0 \
DD_TRACE_DEBUG=true \
DD_PROFILING_CRASHTRACKER_ENABLED=true \
DD_PROFILING_CRASHTRACKER_STDOUT_FILENAME=$OUTFILE \
DD_PROFILING_CRASHTRACKER_STDERR_FILENAME=$ERRFILE \
DD_PROFILING_CRASHTRACKER_STACKTRACE_RESOLVER=full \
DD_SERVICE=my_crash_service_$TEST \
DD_ENV=crash_env \
DD_VERSION=crash_version \
pyenv exec ddtrace-run python tests/$TEST.py
