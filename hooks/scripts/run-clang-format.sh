#!/usr/bin/env sh
# clang-format version CI pins via the lint dependency group (clang-format==18.1.5).
CFORMAT_VERSION=18.1.5
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(c|h|cc|cpp|hpp)$' | tr '\n' ' ')
if [ -n "$(printf '%s' "$staged_files" | tr -d ' \t\n')" ]; then
    file_count=$(echo "$staged_files" | wc -w | tr -d ' ')
    echo "Formatting $file_count staged C/C++ file(s)..."
    # Resolve a clang-format binary, preferring the version pinned in the lint
    # dependency group (clang-format==18.1.5) so local formatting matches CI, which
    # runs `scripts/lint cformat`. An unpinned PATH binary reformats differently
    # (e.g. brace-init layout) and would pass here while failing CI, so the PATH
    # fallback is only accepted when it reports the pinned version.
    # Resolution order:
    #   1. $CFORMAT_BIN                       - explicit override (same knob cformat.sh uses)
    #   2. <repo>/.venv-lint/bin/clang-format - the uv-managed lint env CI uses
    #   3. clang-format on PATH               - only if it reports the pinned version;
    #                                           otherwise fail rather than let drift reach CI
    if [ -n "${CFORMAT_BIN:-}" ]; then
        clang_format="$CFORMAT_BIN"
    else
        repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo .)
        if [ -x "$repo_root/.venv-lint/bin/clang-format" ]; then
            clang_format="$repo_root/.venv-lint/bin/clang-format"
        elif command -v clang-format >/dev/null 2>&1 && clang-format --version | grep -qF "$CFORMAT_VERSION"; then
            clang_format="clang-format"
        else
            echo "run-clang-format: no clang-format matching the version CI enforces (clang-format==${CFORMAT_VERSION}) was found. Run \`scripts/lint cformat\` once to create .venv-lint, or set CFORMAT_BIN to a matching binary, then re-commit. Refusing to format with an unpinned formatter so local output can't drift from CI." >&2
            exit 1
        fi
    fi

    # Skip re-adding files that already had unstaged changes to preserve partial staging.
    unstaged_before=$(git diff --name-only)

    "$clang_format" -i $staged_files || exit $?

    for f in $staged_files; do
        [ -n "$f" ] || continue
        if echo "$unstaged_before" | grep -qFx "$f"; then
            continue
        fi
        git add "$f" || exit $?
    done
else
    echo 'Run clang-format skipped: No C/C++ files were found in `git diff --staged`'
fi
