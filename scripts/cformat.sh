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

git ls-files '*.c' '*.cpp' '*.h' | grep -v '^ddtrace/vendor/' | while read filename
do

if [[ "$1" == "update" ]]
then
    clang-format --style="{BasedOnStyle: Mozilla, IndentWidth: 4, ColumnLimit: 120}" -i "$filename"
else
    CFORMAT_TMP=`mktemp`
    clang-format --style="{BasedOnStyle: Mozilla, IndentWidth: 4, ColumnLimit: 120}" "$filename" > "$CFORMAT_TMP"
    diff -u "$filename" "$CFORMAT_TMP"
    rm -f "$CFORMAT_TMP"
fi
done

