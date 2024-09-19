set -o xtrace

PYTHON="${PYTHON_VERSION:-python3.11d}"
${PYTHON} -m ddtrace.commands.ddtrace_run ${PYTHON} scripts/iast/test_references.py