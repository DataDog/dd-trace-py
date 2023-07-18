#!/bin/bash
set -e

cppcheck --error-exitcode=1 --std=c++17 --language=c++ --force $(git ls-files '*.c' '*.cpp' '*.h' | grep -E -v '^src/ddtrace/(vendor|internal)/' | grep -v '^src/ddtrace/appsec/iast/_taint_tracking/_vendor/')
