#!/usr/bin/env sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(c|h|cc|cpp|hpp)$' | tr '\n' ' ')
if [ -n "$(printf '%s' "$staged_files" | tr -d ' \t\n')" ]; then
    file_count=$(echo "$staged_files" | wc -w | tr -d ' ')
    echo "Formatting $file_count staged C/C++ file(s)..."
    # Skip re-adding files that already had unstaged changes to preserve partial staging.
    unstaged_before=$(git diff --name-only)

    clang-format -i $staged_files || exit $?

    echo "$staged_files" | tr ' ' '\n' | while read -r f; do
        [ -n "$f" ] || continue
        echo "$unstaged_before" | grep -qFx "$f" || git add "$f"
    done
else
    echo 'Run clang-format skipped: No C/C++ files were found in `git diff --staged`'
fi

# Catch tracked-but-unstaged C/C++ edits the hook above would otherwise skip.
dirty_cpp=$(git diff --name-only --diff-filter=ACMR | grep -E '\.(c|h|cc|cpp|hpp)$' | tr '\n' ' ')
if [ -n "$(printf '%s' "$dirty_cpp" | tr -d ' \t\n')" ]; then
    has_diff=0
    for f in $dirty_cpp; do
        tmp=$(mktemp)
        clang-format "$f" > "$tmp"
        if ! diff -q "$f" "$tmp" >/dev/null 2>&1; then
            has_diff=1
            echo "Unstaged file has clang-format issues: $f"
        fi
        rm -f "$tmp"
    done
    if [ "$has_diff" -ne 0 ]; then
        echo "Fix: scripts/cformat.sh fix <file>, or stage the file and commit again."
        exit 1
    fi
fi
