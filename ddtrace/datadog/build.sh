#!/bin/bash
DDPROF_PATH=./libdatadog-x86_64-unknown-linux-gnu.v2.0
g++-11 -std=c++20 \
  -shared -fPIC \
  -O3 \
  -ggdb3 \
  -Wall -Wextra \
  -Iinclude \
  -I${DDPROF_PATH}/include \
  -L${DDPROF_PATH}/lib \
  src/exporter.cpp \
  -o libddup.so \
  -l:libdatadog_profiling.a
