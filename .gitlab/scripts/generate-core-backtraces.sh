#!/usr/bin/env bash
# Generate human-readable backtraces from core dump files.
# Produces a .bt.txt file alongside each core.* file.

set -o pipefail

for core_file in core.*; do
    # Skip non-files (e.g. if the glob matched nothing) and our own output files
    [ -f "$core_file" ] || continue
    case "$core_file" in *.bt.txt) continue;; esac

    output="${core_file}.bt.txt"
    echo "Processing $core_file ..."

    # Extract the executable path recorded in the core file
    exe=$(file "$core_file" | grep -oP "execfn: '\K[^']+")
    if [ -z "$exe" ] || [ ! -f "$exe" ]; then
        echo "Could not find executable for $core_file (exe=${exe:-<not found>})" > "$output"
        continue
    fi

    gdb -batch \
        -ex "set auto-load safe-path /" \
        -ex "set pagination off" \
        -ex "file $exe" \
        -ex "core-file $core_file" \
        -ex "bt" \
        -ex "info threads" \
        -ex "thread apply all bt full" \
        > "$output" 2>&1

    echo "  -> $output ($(wc -l < "$output") lines)"
done
