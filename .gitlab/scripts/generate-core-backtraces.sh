#!/usr/bin/env bash
# Generate human-readable backtraces from core dump files.
# Produces a .bt.txt file alongside each core file.
#
# Assumes core files are named core.* (i.e. kernel.core_pattern = "core.%p").
# This matches the CI testrunner configuration.

set -o pipefail

for core_file in core.*; do
    # Skip non-files (e.g. if the glob matched nothing) and our own output files
    [ -f "$core_file" ] || continue
    case "$core_file" in *.bt.txt) continue;; esac

    output="${core_file}.bt.txt"
    echo "Processing $core_file ..."

    # Extract the executable path from the core file's ELF auxiliary vector via gdb
    exe=$(gdb -batch -ex "core-file $core_file" -ex "info auxv" 2>/dev/null \
        | grep AT_EXECFN | grep -oP '"\K[^"]+')

    # Since tests can be run with "pytest" or any other script that's just a
    # wrapper, AT_EXECFN may point to the script rather than the Python binary.
    # Follow the shebang to find the actual interpreter.
    if [ -n "$exe" ] && [ -f "$exe" ] && head -c2 "$exe" | grep -q '^#!'; then
        interp=$(head -1 "$exe" | sed 's/^#!//' | awk '{print $1}')
        if [ -n "$interp" ] && [ -f "$interp" ]; then
            exe="$interp"
        fi
    fi

    if [ -z "$exe" ] || [ ! -f "$exe" ]; then
        echo "Could not find executable for $core_file (exe=${exe:-<not found>})" > "$output"
        continue
    fi

    gdb -batch \
        -ex "set auto-load safe-path /" \
        -ex "file $exe" \
        -ex "core-file $core_file" \
        -ex "bt" \
        -ex "info threads" \
        -ex "thread apply all bt full" \
        > "$output" 2>&1

    echo "  -> $output ($(wc -l < "$output") lines)"
done
