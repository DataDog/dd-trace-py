#!/bin/sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(py|pyi|pyx)$' | tr '\n' ' ')
if [ -n "$staged_files" ]; then
    file_count=$(echo "$staged_files" | wc -w | tr -d ' ')
    echo "Formatting and linting $file_count staged Python/Cython/stub file(s)..."
    # Capture files with unstaged changes before ruff; we skip re-adding these to preserve partial staging
    unstaged_before=$(git diff --name-only)

    # ruff runs on .py and .pyi files only — ruff does not support Cython syntax in .pyx files
    # (see https://github.com/astral-sh/ruff/issues/10250)
    staged_ruff=$(echo "$staged_files" | tr ' ' '\n' | grep -E '\.(py|pyi)$' | grep -v '^$' | tr '\n' ' ')
    if [ -n "$(printf '%s' "$staged_ruff" | tr -d ' \t\n')" ]; then
        # shellcheck disable=SC2086  # Intentional word-splitting: $staged_ruff is a space-separated list of filenames
        hatch -v run lint:ruff format --no-cache $staged_ruff || exit $?
        # shellcheck disable=SC2086
        hatch -v run lint:ruff check --fix --show-fixes --no-cache $staged_ruff || exit $?
    fi

    # cython-lint runs on .pyx files only; ruff covers .py/.pyi, and cython-lint's
    # pycodestyle checks would be redundant on .py files
    staged_cython_lint=$(echo "$staged_files" | tr ' ' '\n' | grep '\.pyx$' | grep -v '^$' | tr '\n' ' ')
    if [ -n "$(printf '%s' "$staged_cython_lint" | tr -d ' \t\n')" ]; then
        # shellcheck disable=SC2086  # Intentional word-splitting: $staged_cython_lint is a space-separated list of filenames
        hatch -v run lint:cython-lint $staged_cython_lint || exit $?
    fi

    # Re-stage only files that had no unstaged changes before ruff (preserves partial staging)
    echo "$staged_files" | tr ' ' '\n' | while read -r f; do
        [ -n "$f" ] || continue
        echo "$unstaged_before" | grep -qFx "$f" || git add "$f"
    done
else
    # shellcheck disable=SC2016  # Backtick in message is literal, not a command substitution
    echo 'Format/lint skipped: No Python/stub files were found in `git diff --staged`'
fi
