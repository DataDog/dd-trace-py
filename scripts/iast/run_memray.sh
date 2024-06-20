PYTHON="${PYTHON_VERSION:-python3.11}"
$PYTHON -m pip install -r scripts/iast/requirements.txt
$PYTHON -m memray run --trace-python-allocators --aggregate --native -o lel.bin -f scripts/iast/test_leak_functions.py 100
$PYTHON -m memray flamegraph lel.bin --leaks -f