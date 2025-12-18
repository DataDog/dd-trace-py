#!/usr/bin/env bash

source venv311/bin/activate

export DD_PROFILING_ENABLED=1

ddtrace-run python -c "import ddtrace; print(ddtrace.__version__)"
ddtrace-run python scrip/whatever.py