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
    THIS_DIR="$(dirname $(dirname "$THIS_PATH"))"
    for file in $(find "$THIS_DIR" -name '*.[c|cpp|h]' | grep -v '.riot/' | grep -v 'ddtrace/vendor/' | grep -v 'ddtrace/appsec/_iast/_taint_tracking/cmake-build-debug/' | grep -v '^ddtrace/appsec/_iast/_taint_tracking/_vendor/')
    do
    clang-format --style="{BasedOnStyle: Mozilla, IndentWidth: 4, ColumnLimit: 120}" -i "$file"
    done
else
  git ls-files '*.c' '*.cpp' '*.h' | grep -v '^ddtrace/vendor/' | grep -v '^ddtrace/appsec/_iast/_taint_tracking/_vendor/'  | while read filename
  do
    CFORMAT_TMP=`mktemp`
    clang-format --style="{BasedOnStyle: Mozilla, IndentWidth: 4, ColumnLimit: 120}" "$filename" > "$CFORMAT_TMP"
    diff -u "$filename" "$CFORMAT_TMP"
    rm -f "$CFORMAT_TMP"
  done
fi

