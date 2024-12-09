set -o xtrace

PYTHON="${PYTHON_VERSION:-python3.11d}"
$PYTHON -m pip install -r scripts/iast/requirements.txt
export DD_IAST_ENABLED=true
export _DD_IAST_DEBUG=true
${PYTHON} -m ddtrace.commands.ddtrace_run ${PYTHON} scripts/iast/test_references.py