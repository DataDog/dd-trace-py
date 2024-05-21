# python3.11 setup.py build_ext --inplace
python3.11 -m pip install -r scripts/iast/requirements.txt
python3.11 -m ddtrace.commands.ddtrace_run python3.11 scripts/iast/test_references.py