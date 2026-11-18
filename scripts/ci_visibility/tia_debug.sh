#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

if [[ "$#" -eq 0 || "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'USAGE'
Run scripts/run-tests with TIA debugging against staging.

Export DD_API_KEY before running; DD_SITE defaults to datad0g.com.
Pass the same runner and pytest arguments on each run to reuse the map.
Choose a Python 3.12+ environment from scripts/run-tests --list.

Usage:
  DD_API_KEY=... DD_SITE=datad0g.com scripts/ci_visibility/tia_debug.sh \
      --venv <environment-hash> -- --ddtrace -n 0

Use -n 0 for suites that include pytest-xdist; TIA requires a single process.
Omit -n 0 when the environment does not include pytest-xdist.
Add DD_TRACE_DEBUG=1 for broader tracer logging. All arguments are forwarded
unchanged. Existing test environments are reused unless you pass -r.
USAGE
    exit 0
fi

# Discovery does not start pytest or require backend credentials.
for argument in "$@"; do
    [[ "$argument" != "--" ]] || break
    if [[ "$argument" == "--list" ]]; then
        exec "$PROJECT_ROOT/scripts/run-tests" "$@"
    fi
done

: "${DD_API_KEY:?Export DD_API_KEY with a staging API key before running this script}"
export DD_SITE="${DD_SITE:-datad0g.com}"
export DD_TIA_ENABLED=1
export DD_TEST_DEBUG=1
export DD_CIVISIBILITY_ENABLED=1
export DD_CIVISIBILITY_AGENTLESS_ENABLED=1

# Use the supplied key and site rather than inherited CI-only routing overrides.
unset _CI_DD_API_KEY DD_CIVISIBILITY_AGENTLESS_URL

printf 'TIA debugging: DD_SITE=%s, DD_TEST_DEBUG=1, agentless mode enabled\n' "$DD_SITE" >&2
exec "$PROJECT_ROOT/scripts/run-tests" "$@"
