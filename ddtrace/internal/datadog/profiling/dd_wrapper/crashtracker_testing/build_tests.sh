#!/bin/bash
# NOTICE:  these are currently all offline tests
mkdir -p build
PY_EXE=$(realpath $(pyenv which python))
PY_INC=$(python -c "from distutils.sysconfig import get_python_inc; print(get_python_inc())")
PY_LIB=$(python -c "from distutils.sysconfig import get_config_var; print(get_config_var('LIBDIR'))")


# Build tests
for i in 0{1..8}; do
  gcc -shared -fPIC -DLIB_NAME=lib$i -o build/lib$i.so -Iinclude -I$PY_INC src/$i.c -L$PY_LIB -lpython3
done
