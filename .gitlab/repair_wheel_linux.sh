#!/bin/bash
set -e
wheelhouse="${1:-wheelhouse/}"

rm -rf tempwheelhouse/
mkdir tempwheelhouse/

for w in $(ls wheelhouse/*.whl);
do
    echo "Repairing $w"
    unzip -l "$w" | grep -E '(\.so)|(\*.c)|(\*.cpp)|(\*.cc)|(\*.h)|(\*.hpp)|(\*.pyx)'
    auditwheel repair -w tempwheelhouse/ "$w"
done

for new_wheel in tempwheelhouse/*.whl; do
    echo "Removing source files from $new_wheel"
    python scripts/zip_filter.py "$new_wheel" \*.c \*.cpp \*.cc \*.h \*.hpp \*.pyx
    unzip -l "$new_wheel" | grep -E '(\.so)|(\*.c)|(\*.cpp)|(\*.cc)|(\*.h)|(\*.hpp)|(\*.pyx)'
    mv "$new_wheel" wheelhouse/
done

rm -rf tempwheelhouse/
