#!/usr/bin/env bash
set -e -o pipefail
wheelhouse="${1:-wheelhouse/}"

# First repair the wheels
# This will convert linux_{arch}.whl to manylinux_{arch}.whl, and the delete the
# original linux_{arch}.whl
for w in $(ls wheelhouse/*.whl);
do
    echo "Repairing $w"
    unzip -l "$w" | grep -E '(\.so)|(\*.c)|(\*.cpp)|(\*.cc)|(\*.h)|(\*.hpp)|(\*.pyx)'
    auditwheel repair -w wheelhouse/ "$w"
    rm "$w"
done

# Strip any source files from the manylinux wheels
for w in $(ls wheelhouse/*.whl); do
    echo "Removing source files from $w"
    python3.12 scripts/zip_filter.py "$w" \*.c \*.cpp \*.cc \*.h \*.hpp \*.pyx
    unzip -l "$w" | grep -E '(\.so)|(\*.c)|(\*.cpp)|(\*.cc)|(\*.h)|(\*.hpp)|(\*.pyx)'
done
