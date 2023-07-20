#!/bin/bash -e
for file in src/ddtrace/profiling/_build.pyx \
    src/ddtrace/profiling/exporter/pprof.pyx; do
    stubgen "$file"
    mv out/__main__.pyi $(dirname "$file")/$(basename "$file" .pyx).pyi
done
rmdir out
