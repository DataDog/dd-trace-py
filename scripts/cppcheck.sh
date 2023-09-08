#!/bin/bash
set -e

cppcheck --error-exitcode=1 --std=c++17 --language=c++ --force \
    $(git ls-files '*.c' '*.cpp' '*.h' '*.hpp' '*.cc' '*.hh' | grep -E -v '^(ddtrace/(vendor|internal)|ddtrace/appsec/iast/_taint_tracking/_vendor)/')
