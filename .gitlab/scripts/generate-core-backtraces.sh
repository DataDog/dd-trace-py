#!/usr/bin/env bash
# Collect core dumps into core-dumps/ and write a human-readable backtrace
# (<core>.bt.txt) next to each one.
#
# Cores are looked up where kernel.core_pattern puts them: the pattern's
# directory, which for a relative pattern (e.g. core.%p) is relative to the
# crashing process's working directory.

set -o pipefail

out_dir="$(pwd)/core-dumps"

core_pattern=$(cat /proc/sys/kernel/core_pattern)
echo "kernel.core_pattern=${core_pattern}"

case "$core_pattern" in
    "|"*)
        echo "core_pattern pipes core dumps to a host handler; they can't be collected from the job container."
        exit 0
        ;;
esac
core_dir=$(dirname "$core_pattern")

if [ ! -d "$core_dir" ] || [ ! -w "$core_dir" ]; then
    echo "${core_dir} does not exist or is not writable by $(id -un); the kernel cannot write core dumps there."
    exit 0
fi

# Literal file name prefix of the pattern, before the first % specifier.
prefix=$(basename "$core_pattern")
prefix=${prefix%%%*}

# Artifacts must be under the project directory, so copy the cores there.
# Copy them all before running gdb, so a slow or killed gdb can't keep the
# remaining cores from being uploaded.
mkdir -p "$out_dir"
cp "$core_dir/$prefix"* "$out_dir/" 2>/dev/null
# The kernel writes cores as 0600.
chmod -R a+r "$out_dir"

for core_file in "$out_dir"/*; do
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
