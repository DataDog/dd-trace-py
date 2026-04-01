#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(py|pyi|pyx)$' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    file_count=$(echo "$staged_files" | wc -w | tr -d ' ')
    echo "Formatting and linting $file_count staged Python/Cython/stub file(s)..."
    # Capture files with unstaged changes before ruff; we skip re-adding these to preserve partial staging
    unstaged_before=$(git diff --name-only)

    # ruff runs on .py, .pyi, and .pyx files
    hatch -v run lint:ruff format --no-cache $staged_files || exit $?
    hatch -v run lint:ruff check --fix --show-fixes --no-cache $staged_files || exit $?

    # cython-lint runs on .py and .pyx files; .pyi stubs use ruff's compact stub formatting
    # which conflicts with cython-lint's PEP 8 E301/E302 blank line rules
    staged_cython_lint=$(echo "$staged_files" | tr ' ' '\n' | grep -v '\.pyi$' | grep -v '^$' | tr '\n' ' ')
    if [ -n "$(printf '%s' "$staged_cython_lint" | tr -d ' \t\n')" ]; then
        hatch -v run lint:cython-lint $staged_cython_lint || exit $?
    fi

    # Re-stage only files that had no unstaged changes before ruff (preserves partial staging)
    echo "$staged_files" | tr ' ' '\n' | while read -r f; do
        [ -n "$f" ] || continue
        echo "$unstaged_before" | grep -qFx "$f" || git add "$f"
    done
else
    echo 'Format/lint skipped: No Python/stub files were found in `git diff --staged`'
fi
