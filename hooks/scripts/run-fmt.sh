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

    # Re-stage ruff's fixes before cython-lint so they're preserved even if cython-lint fails
    echo "$staged_files" | tr ' ' '\n' | while read -r f; do
        [ -n "$f" ] || continue
        echo "$unstaged_before" | grep -qFx "$f" || git add "$f"
    done

    # cython-lint runs on .pyx files only; ruff covers .py/.pyi, and cython-lint's
    # pycodestyle checks would be redundant on .py files
    staged_cython_lint=$(echo "$staged_files" | tr ' ' '\n' | grep '\.pyx$' | grep -v '^$' | tr '\n' ' ')
    if [ -n "$(printf '%s' "$staged_cython_lint" | tr -d ' \t\n')" ]; then
        # shellcheck disable=SC2086  # Intentional word-splitting: $staged_cython_lint is a space-separated list of filenames
        hatch -v run lint:cython-lint $staged_cython_lint || exit $?
    fi
else
    # shellcheck disable=SC2016  # Backtick in message is literal, not a command substitution
    echo 'Format/lint skipped: No Python/stub files were found in `git diff --staged`'
fi

# Check that all tracked-but-unstaged modified Python files are already formatted.
# This catches the case where a file was edited but not staged — the hook above
# would silently skip it, leaving unformatted code in the working tree.
dirty_ruff=$(git diff --name-only --diff-filter=ACMR | grep -E '\.(py|pyi)$' | tr '\n' ' ')
if [ -n "$(printf '%s' "$dirty_ruff" | tr -d ' \t\n')" ]; then
    ruff_output=$(hatch -v run lint:ruff format --check --no-cache $dirty_ruff 2>&1)
    if [ $? -ne 0 ]; then
        RED='\033[0;31m'
        BOLD='\033[1m'
        RESET='\033[0m'
        printf "\n${BOLD}${RED}╔══ FORMAT ERROR ═══════════════════════════════════════════╗${RESET}\n"
        printf "${BOLD}${RED}║  Unstaged file(s) have formatting issues:${RESET}\n"
        echo "$ruff_output" | grep -E "^Would reformat" | while IFS= read -r line; do
            printf "${RED}║    • %s${RESET}\n" "$line"
        done
        printf "${BOLD}${RED}║${RESET}\n"
        printf "${BOLD}${RED}║  Fix: hatch run lint:fmt${RESET}\n"
        printf "${BOLD}${RED}║  Or:  stage the files and let the hook auto-fix them.${RESET}\n"
        printf "${BOLD}${RED}╚═══════════════════════════════════════════════════════════╝${RESET}\n\n"
        exit 1
    fi
fi
