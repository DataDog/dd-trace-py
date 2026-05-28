#!/usr/bin/env sh
# Remind or rebuild in-tree native extensions when staged changes touch product native/build inputs.
#
# Per-repo preference (recommended):
#   git config --local ddtrace.nativeBuildMode warn   # remind only (default)
#   git config --local ddtrace.nativeBuildMode block  # build_ext before commit
#   git config --local ddtrace.nativeBuildMode off    # disable hook
#
# First commit with native files in an interactive terminal prompts once and saves the choice.
# Session overrides: DD_BUILD_NATIVE_ON_COMMIT=1 (block), DD_SKIP_NATIVE_BUILD=1 (off)

set -eu

GIT_CONFIG_KEY='ddtrace.nativeBuildMode'
NATIVE_FILE_PATTERN='\.(c|h|cc|cpp|hpp|rs|pyx|pxd)$|CMakeLists\.txt|\.cmake$|^setup\.py$'
# Product paths only; skip test harness / fuzz trees (still covered by clang-format etc.).
TEST_PATH_EXCLUDE='(^|/)(test|tests|fuzz)(/|$)|/dd_wrapper/test/|_test\.cpp$'

repo_root=$(git rev-parse --show-toplevel)
cd "$repo_root"

if [ "${DD_SKIP_NATIVE_BUILD:-}" = "1" ]; then
    echo 'native build hook skipped: DD_SKIP_NATIVE_BUILD=1'
    exit 0
fi

configured_mode=$(git config --local --get "$GIT_CONFIG_KEY" 2>/dev/null || true)
if [ "$configured_mode" = "off" ]; then
    echo "native build hook skipped: $GIT_CONFIG_KEY=off"
    exit 0
fi

staged_files=$(git diff --staged --name-only HEAD --diff-filter=ACMR)
if [ -z "$staged_files" ]; then
    echo 'native build hook skipped: no staged files'
    exit 0
fi

product_native_files=$(printf '%s\n' "$staged_files" \
    | grep -E "$NATIVE_FILE_PATTERN" \
    | grep -vE "$TEST_PATH_EXCLUDE" || true)

if [ -z "$product_native_files" ]; then
    echo 'native build hook skipped: no staged product native or build files'
    exit 0
fi

is_interactive() {
    [ -t 0 ] \
        && [ -z "${CI:-}" ] \
        && [ -z "${GITHUB_ACTIONS:-}" ] \
        && [ -z "${BUILDKITE:-}" ] \
        && [ -z "${DDTRACE_NATIVE_BUILD_NONINTERACTIVE:-}" ]
}

normalize_mode() {
    case "$1" in
        block|warn|off) printf '%s\n' "$1" ;;
        *) printf '%s\n' 'warn' ;;
    esac
}

maybe_prompt_for_mode() {
    if ! is_interactive; then
        return 0
    fi

    echo ""
    echo "Native build pre-commit hook: choose a default for this repository"
    echo "  [1] warn  - print a rebuild reminder (recommended)"
    echo "  [2] block - run build_ext --inplace before each commit"
    echo "  [3] off   - disable this hook for this repository"
    printf "Choice [1]: "
    IFS= read -r choice || choice=1

    case "$choice" in
        2|block|Block|BLOCK) mode=block ;;
        3|off|Off|OFF) mode=off ;;
        *) mode=warn ;;
    esac

    git config --local "$GIT_CONFIG_KEY" "$mode"
    echo "Saved for this repo: git config --local $GIT_CONFIG_KEY $mode"
    echo ""
}

resolve_native_build_mode() {
    if [ "${DD_BUILD_NATIVE_ON_COMMIT:-}" = "1" ]; then
        printf '%s\n' 'block'
        return 0
    fi

    mode=$(git config --local --get "$GIT_CONFIG_KEY" 2>/dev/null || true)
    if [ -z "$mode" ]; then
        maybe_prompt_for_mode
        mode=$(git config --local --get "$GIT_CONFIG_KEY" 2>/dev/null || true)
    fi

    normalize_mode "${mode:-warn}"
}

resolve_python() {
    if [ -x .venv/bin/python ]; then
        PYTHON=.venv/bin/python
    elif [ -n "${PYTHON:-}" ]; then
        PYTHON=$PYTHON
    else
        PYTHON=python3
    fi
}

warn_staged_native() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "⚠️  STAGED NATIVE/BUILD FILES (rebuild recommended)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "The following staged files may require rebuilt native extensions:"
    echo ""

    printf '%s\n' "$product_native_files" | head -n 10 | sed 's/^/  • /'

    total=$(printf '%s\n' "$product_native_files" | wc -l | tr -d ' ')
    if [ "$total" -gt 10 ]; then
        echo "  ... and $((total - 10)) more file(s)"
    fi

    echo ""
    echo "You may need to rebuild native extensions before running or pushing."
    echo ""
    echo "  python setup.py build_ext --inplace"
    echo "  pip install -e ."
    echo ""
    echo "Change hook behavior for this repo:"
    echo "  git config --local $GIT_CONFIG_KEY block   # rebuild before commit"
    echo "  git config --local $GIT_CONFIG_KEY off     # disable hook"
    echo ""
    echo "post-merge/post-checkout hooks also remind you after pull or branch switch."
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
}

is_stale_eggs_error() {
    printf '%s\n' "$1" | grep -qiE 'directory not empty|errno 66|\[errno 66\]'
}

run_build_ext() {
    set +e
    build_output=$("$PYTHON" setup.py build_ext --inplace 2>&1)
    build_status=$?
    set -e
    return "$build_status"
}

run_blocking_build() {
    resolve_python
    echo "Rebuilding native extensions ($PYTHON setup.py build_ext --inplace)..."
    echo "Triggered by staged product native/build files:"
    printf '%s\n' "$product_native_files" | sed 's/^/  /'
    echo ""

    if run_build_ext; then
        printf '%s\n' "$build_output"
        return 0
    fi

    printf '%s\n' "$build_output" >&2

    if is_stale_eggs_error "$build_output" && [ -d .eggs ]; then
        echo "" >&2
        echo "build_ext failed with a stale .eggs cache; removing .eggs and retrying once..." >&2
        rm -rf .eggs
        build_output=""
        if run_build_ext; then
            printf '%s\n' "$build_output"
            return 0
        fi
        printf '%s\n' "$build_output" >&2
    fi

    return "$build_status"
}

native_build_mode=$(resolve_native_build_mode)

case "$native_build_mode" in
    off)
        echo "native build hook skipped: $GIT_CONFIG_KEY=off"
        exit 0
        ;;
    block)
        run_blocking_build
        exit $?
        ;;
    warn)
        warn_staged_native
        exit 0
        ;;
esac
