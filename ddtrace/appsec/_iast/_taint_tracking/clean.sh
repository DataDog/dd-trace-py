#!/bin/bash
set -exu
#cd -- "$(dirname -- "${BASH_SOURCE[0]}")" || exit

rm -rf CMakeFiles/ CMakeCache.txt Makefile cmake_install.cmake __pycache__/ .cmake *.cbp Testing
rm -rf cmake-build-debug cmake-build-default cmake-build-tests
yes|rm -f *.so
