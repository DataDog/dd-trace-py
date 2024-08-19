#!/bin/bash
set -e

# For more modern versions:
# clang-format --dry-run -Werror file.c
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
  # Find .c, , .h, .cpp, and .hpp files, excluding specified directories
  find "$THIS_DIR" -type f \( -name '*.c' -o -name '*.h' -o -name '*.cpp' -o -name '*.hpp' \) \
    | grep -v '.eggs/' \
    | grep -v 'dd-trace-py/build/' \
    | grep -v '_taint_tracking/CMakeFiles' \
    | grep -v '_taint_tracking/_deps/' \
    | grep -v '.riot/' \
    | grep -v 'ddtrace/vendor/' \
    | grep -v '_taint_tracking/_vendor/' \
    | grep -v 'ddtrace/appsec/_iast/_taint_tracking/cmake-build-debug/' \
    | grep -v '^ddtrace/appsec/_iast/_taint_tracking/_vendor/' \
    | while IFS= read -r file; do
  clang-format -i $file
  echo "Formatting $file"
done
else
  git ls-files '*.c' '*.h' '*.cpp' '*.hpp' | grep -v '^ddtrace/vendor/' | grep -v '^ddtrace/appsec/_iast/_taint_tracking/_vendor/'  | while read filename
  do
    CFORMAT_TMP=`mktemp`
    clang-format "$filename" > "$CFORMAT_TMP"
    diff -u "$filename" "$CFORMAT_TMP"
    rm -f "$CFORMAT_TMP"
  done
fi

