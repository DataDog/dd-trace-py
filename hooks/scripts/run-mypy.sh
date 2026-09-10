#!/bin/sh
LINT_CMD="${LINT_CMD:-scripts/lint}"
# Skip tests/: CI `scripts/lint typing` only checks ddtrace + docs (mypy.ini files=).
# Staging a tests/ file makes mypy follow-import ddtrace in a different order
# and fail [has-type], which aborts the whole hook suite including ruff I001.
# tests/tracer/runtime is covered by 11-run-mypy-tracer-runtime-tests.
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(py|pyi)$' | grep -v '^tests/' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    # Drop .pyi stubs whose .py counterpart is also staged to avoid mypy
    # "Duplicate module named ..." errors. mypy discovers stubs automatically.
    deduped=""
    for f in $staged_files; do
        case "$f" in
            *.pyi)
                py="${f%i}"
                case " $staged_files " in
                    *" $py "*) continue ;;
                esac
                ;;
        esac
        deduped="$deduped $f"
    done
    deduped="${deduped# }"
    if [ -n "$deduped" ]; then
        "$LINT_CMD" typing -- $deduped
    else
        echo 'Run mypy skipped: all staged stubs have corresponding .py files'
    fi
else
    echo 'Run mypy skipped: No production Python files were found in `git diff --staged`'
fi
