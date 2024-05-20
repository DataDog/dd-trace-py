export PATH=$PATH:$PWD
export PYTHONPATH=$PYTHONPATH:$PWD
export PYTHONMALLOC=malloc
export DD_COMPILE_DEBUG=true
export DD_TRACE_ENABLED=true
export DD_IAST_ENABLED=true
export _DD_IAST_DEBUG=true
export DD_IAST_REQUEST_SAMPLING=100
export _DD_APPSEC_DEDUPLICATION_ENABLED=false
export DD_INSTRUMENTATION_TELEMETRY_ENABLED=false
export DD_REMOTE_CONFIGURATION_ENABLED=false

# python3.11 setup.py build_ext --inplace
python3.11 -m pip install -r scripts/iast/requirements.txt
python3.11 -m ddtrace.commands.ddtrace_run python3.11 scripts/iast/test_references.py