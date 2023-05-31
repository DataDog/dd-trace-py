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

if [[ "$1" == "update" ]]
then
    THIS_PATH="$(realpath "$0")"
    THIS_DIR="$(dirname "$THIS_PATH")"
    FILE_LIST="$(find "$THIS_DIR" | grep -E ".*(\.ino|\.cpp|\.c|\.h|\.hpp|\.hh)$")"
    clang-format --style="{BasedOnStyle: Mozilla, IndentWidth: 4, ColumnLimit: 120}" -i "$FILE_LIST"
else
  git ls-files '*.c' '*.cpp' '*.h' | grep -v '^ddtrace/vendor/' | while read filename
  do
    CFORMAT_TMP=`mktemp`
    clang-format --style="{BasedOnStyle: Mozilla, IndentWidth: 4, ColumnLimit: 120}" "$filename" > "$CFORMAT_TMP"
    diff -u "$filename" "$CFORMAT_TMP"
    rm -f "$CFORMAT_TMP"
  done
fi

