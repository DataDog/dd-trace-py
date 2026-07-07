#!/usr/bin/env bash
set -euo pipefail

# Build an isolated environment, install ddtrace, and run a fork-deadlock
# reproducer.
#
# Env vars:
#   REPRO_VENV      Path to reuse/create venv (default: .repro-venv-fork-deadlock)
#   DDTRACE_SOURCE  Package spec to install (default: ddtrace==4.9.1)
#                  Examples: DDTRACE_SOURCE=ddtrace==3.12.4, DDTRACE_SOURCE=/path/to/dd-trace-py
#   REPRO_SCRIPT    Script to run (default: scripts/repro_fork_deadlock_logging.py)
#   REPRO_DD_TRACE_DEBUG Enable ddtrace debug logs (default: true)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPRO_VENV="${REPRO_VENV:-${ROOT_DIR}/.repro-venv-fork-deadlock}"
DDTRACE_SOURCE="${DDTRACE_SOURCE:-ddtrace==4.9.1}"
REPRO_SCRIPT="${REPRO_SCRIPT:-${ROOT_DIR}/scripts/repro_fork_deadlock_logging.py}"
REPRO_DD_TRACE_DEBUG="${REPRO_DD_TRACE_DEBUG:-true}"

if [[ ! -d "${REPRO_VENV}" ]]; then
  python3 -m venv "${REPRO_VENV}"
fi

"${REPRO_VENV}/bin/python" -m pip install -U pip setuptools wheel
"${REPRO_VENV}/bin/python" -m pip install "${DDTRACE_SOURCE}"

set +e
DD_TRACE_ENABLED=true DD_TRACE_DEBUG="${REPRO_DD_TRACE_DEBUG}" "${REPRO_VENV}/bin/python" "${REPRO_SCRIPT}"
status=$?
set -e

if [[ ${status} -eq 124 ]]; then
  echo "reproducer observed the deadlock (exit 124)"
else
  echo "reproducer did not observe the deadlock (exit ${status})"
fi

exit "${status}"
