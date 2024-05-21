python3.11 -m pip install -r scripts/iast/requirements.txt
python3.11 -m memray run --trace-python-allocators --aggregate --native -o lel.bin -f scripts/iast/test_leak_functions.py 100
python3.11 -m memray flamegraph lel.bin --leaks -f