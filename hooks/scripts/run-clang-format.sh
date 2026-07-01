#!/usr/bin/env sh
staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR | grep -E '\.(c|h|cc|cpp|hpp)$')
if [ -n "$staged_files" ]; then
    # Resolve a clang-format binary, preferring the version pinned in the lint
    # dependency group (clang-format==18.1.5) so local formatting matches CI, which
    # runs `scripts/lint cformat`. Using an unpinned PATH binary silently reformats
    # differently (e.g. brace-init layout) and passes here while failing CI.
    # Resolution order:
    #   1. $CFORMAT_BIN                       - explicit override (same knob cformat.sh uses)
    #   2. <repo>/.venv-lint/bin/clang-format - the uv-managed lint env CI uses
    #   3. clang-format on PATH               - may differ from CI; warn so drift is visible
    if [ -n "${CFORMAT_BIN:-}" ]; then
        clang_format="$CFORMAT_BIN"
    else
        repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo .)
        if [ -x "$repo_root/.venv-lint/bin/clang-format" ]; then
            clang_format="$repo_root/.venv-lint/bin/clang-format"
        else
            clang_format="clang-format"
            echo 'run-clang-format: using clang-format from PATH; it may differ from the version CI enforces (clang-format==18.1.5). Run `scripts/lint cformat` once to create .venv-lint, or set CFORMAT_BIN, to match CI.' >&2
        fi
    fi
    "$clang_format" -i $staged_files
else
    echo 'Run clang-format skipped: No C/C++ files were found in `git diff --staged`'
fi
