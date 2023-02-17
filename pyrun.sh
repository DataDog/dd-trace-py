#!/bin/bash

SCRIPTPATH=$(readlink -f "$0")

export ROOT_DIR=$(dirname $SCRIPTPATH)/libddupload
export LD_LIBRARY_PATH=${LD_LIBRARY_PATH}:$ROOT_DIR:$ROOT_DIR/libdatadog-x86_64-unknown-linux-gnu.v2.0/lib/

export DD_PROFILING_HEAP_ENABLED=false
export DD_PROFILING_MEMORY_ENABLED=false
export DD_PROFILING_MAX_FRAMES=500
export DD_PROFILING_MAX_TIME_USAGE_PCT=100
export PYNAME=/home/ubuntu/.pyenv/versions/3.9.13/envs/ddtrace-clone/bin/python

#/tmp/ddprof/bin/ddprof -S libdatadog_py_native -e sCPU ${PYNAME} tests/profiling/collatz.py
#strace -f -o /tmp/test.out -s 25000 -v ${PYNAME} -u tests/profiling/collatz.py

#gdb --ex "run" --args ${PYNAME} -u tests/profiling/collatz.py
${PYNAME} -u "$@"
