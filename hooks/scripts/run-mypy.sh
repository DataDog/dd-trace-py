#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(py|pyi)$' | tr '\n' ' ')
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
        hatch -v run lint:typing -- $deduped
    else
        echo 'Run mypy skipped: all staged stubs have corresponding .py files'
    fi
else
    echo 'Run mypy skipped: No Python/stub files were found in `git diff --staged`'
fi
