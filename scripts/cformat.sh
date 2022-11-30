#!/bin/bash
set -e

# For more modern versions:
# clang-format --style="\{BasedOnStyle: Mozilla, IndentWidth: 4, ColumnLimit: 120\}" --dry-run -Werror file.c
# would be enough…

clean ()
{
    rm -f "$CFORMAT_TMP"
}

trap clean EXIT

git ls-files '*.c' | grep -v '^ddtrace/vendor/' | while read filename
do
    CFORMAT_TMP=`mktemp`
    clang-format --style="{BasedOnStyle: Mozilla, IndentWidth: 4, ColumnLimit: 120}" "$filename" > "$CFORMAT_TMP"
    diff -u "$filename" "$CFORMAT_TMP"
    rm -f "$CFORMAT_TMP"
done
