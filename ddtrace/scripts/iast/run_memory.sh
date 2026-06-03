set -o xtrace

PYTHON="${PYTHON_VERSION:-python3.11}"
$PYTHON -m pip install -r scripts/iast/requirements.txt
export DD_IAST_ENABLED=true
export _DD_IAST_DEBUG=true
$PYTHON scripts/iast/leak_functions.py --iterations 1000000 --print_every 250