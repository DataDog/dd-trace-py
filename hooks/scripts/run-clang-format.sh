#!/usr/bin/env sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(c|h|cc|cpp|hpp)$')
if [ -n "$staged_files" ]; then
    # Capture files with unstaged changes before formatting; skip re-adding those to preserve partial staging.
    unstaged_before=$(git diff --name-only)

    clang-format -i $staged_files

    # Re-stage only files that had no unstaged changes before formatting.
    echo "$staged_files" | while read -r f; do
        [ -n "$f" ] || continue
        echo "$unstaged_before" | grep -qFx "$f" || git add "$f"
    done
else
    echo 'Run clang-format skipped: No C/C++ files were found in `git diff --staged`'
fi
