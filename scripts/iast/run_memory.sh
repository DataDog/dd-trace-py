set -o xtrace

PYTHON="${PYTHON_VERSION:-python3.11}"
$PYTHON -m pip install -r scripts/iast/requirements.txt
$PYTHON scripts/iast/test_leak_functions.py --iterations 1000000 --print_every 250