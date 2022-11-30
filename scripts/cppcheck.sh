#!/bin/bash
set -e

cppcheck --error-exitcode=1 --force $(git ls-files '*.[ch]' | grep -E -v '^ddtrace/(vendor|internal)/')
