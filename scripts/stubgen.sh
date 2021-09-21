#!/bin/bash -e
for file in ddtrace/profiling/_build.pyx \
                ddtrace/profiling/exporter/pprof.pyx
do
    stubgen "$file"
    mv out/__main__.pyi $(dirname "$file")/$(basename "$file" .pyx).pyi
done
rmdir out
