PYTHON="${PYTHON_VERSION:-python3.11}"
# $PYTHON setup.py build_ext --inplace
${PYTHON} -m pip install -r scripts/iast/requirements.txt
${PYTHON} -m ddtrace.commands.ddtrace_run ${PYTHON} scripts/iast/test_references.py