#!/bin/sh
# Verify that every staged .pyx file has a corresponding .pyi type stub.
# Without a stub, mypy cannot resolve imports from Cython extension modules,
# producing "has no attribute" errors in downstream .py files.

pyx_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.pyx$')
if [ -z "$pyx_files" ]; then
    # shellcheck disable=SC2016  # Backtick in message is literal, not a command substitution
    echo 'Cython stub check skipped: No .pyx files were found in `git diff --staged`'
    exit 0
fi

staged_all=$(git diff --staged --name-only HEAD)
missing=0

for pyx in $pyx_files; do
    pyi="${pyx%.pyx}.pyi"
    # Accept the stub if it exists on disk or is also being staged
    if [ ! -f "$pyi" ] && ! echo "$staged_all" | grep -qFx "$pyi"; then
        echo "Missing type stub: $pyi (required for $pyx)"
        missing=$((missing + 1))
    fi
done

if [ "$missing" -gt 0 ]; then
    echo ""
    echo "$missing Cython file(s) missing .pyi type stubs."
    echo "Without stubs, mypy will report 'has no attribute' for any module that imports them."
    echo "Add a .pyi stub exporting the public API for each."
    exit 1
fi
