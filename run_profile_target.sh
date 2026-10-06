#!/usr/bin/env bash
# Start profile_target.py with the profiler on. Profiles go to ./profiles/profile.<pid>.<n>
# every UPLOAD_INTERVAL seconds (default 5). Edit ./profiler_config.json while it runs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILES_DIR="$(pwd)/profiles"
mkdir -p "$PROFILES_DIR"

if [[ ! -f profiler_config.json ]]; then
    printf '{\n    "stack": true,\n    "memory": true,\n    "lock": true,\n    "exception": true\n}\n' | jq > profiler_config.json
fi

export DD_PROFILING_ENABLED=true
export DD_PROFILING_OUTPUT_PPROF="$PROFILES_DIR/profile"
export DD_PROFILING_UPLOAD_INTERVAL="${UPLOAD_INTERVAL:-5}"
export DD_TRACE_ENABLED=true

exec ddtrace-run python "$SCRIPT_DIR/profile_target.py" "$@"
