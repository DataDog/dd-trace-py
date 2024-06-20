#!/bin/bash
# NOTICE:  these are currently all offline tests
mkdir -p tests/build
PY_EXE=$(realpath $(pyenv which python))
PY_INC=$(python -c "from distutils.sysconfig import get_python_inc; print(get_python_inc())")
PY_LIB=$(python -c "from distutils.sysconfig import get_config_var; print(get_config_var('LIBDIR'))")


# Build tests
for i in 0{1..8}; do
  echo "Building test $i"
  gcc -shared -fPIC -DLIB_NAME=lib$i -o tests/build/lib$i.so -Iinclude -I$PY_INC src/$i.c -L$PY_LIB -lpython3
done
