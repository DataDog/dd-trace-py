#!/usr/bin/env sh
# Tests for hooks/pre-push/01-clang-tidy-profiling
#
# Stubs git and run_clang_tidy.sh so no real toolchain is needed.
# Run with:  sh hooks/tests/test-clang-tidy-pre-push.sh

set -eu

HOOK="$(cd "$(dirname "$0")/../pre-push" && pwd)/01-clang-tidy-profiling"
PASS=0
FAIL=0

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

check() {
    name="$1"
    condition="$2"
    if eval "$condition"; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name"
        FAIL=$((FAIL + 1))
    fi
}

setup_toolchain() {
    clang_major="${1:-18}"
    for cmd in cmake jq run-clang-tidy; do
        printf '#!/usr/bin/env sh\nexit 0\n' > "${TMPDIR_TEST}/${cmd}"
        chmod +x "${TMPDIR_TEST}/${cmd}"
    done
    cat > "${TMPDIR_TEST}/clang-tidy" << EOF
#!/usr/bin/env sh
if [ "\$1" = "--version" ]; then
    echo "clang-tidy version ${clang_major}.1.0"
    exit 0
fi
exit 0
EOF
    chmod +x "${TMPDIR_TEST}/clang-tidy"
}

setup_repo_layout() {
    mkdir -p "${TMPDIR_TEST}/ddtrace/internal/datadog/profiling/build"
    printf '[]\n' > "${TMPDIR_TEST}/ddtrace/internal/datadog/profiling/build/compile_commands.json"
    cat > "${TMPDIR_TEST}/ddtrace/internal/datadog/profiling/run_clang_tidy.sh" << EOF
#!/usr/bin/env sh
if [ "\${DDTRACE_CLANG_TIDY_SKIP_BUILD:-0}" != "1" ]; then
    echo "expected DDTRACE_CLANG_TIDY_SKIP_BUILD=1" >&2
    exit 2
fi
echo run_clang_tidy >> "${TMPDIR_TEST}/clang-tidy-calls.txt"
exit 0
EOF
    chmod +x "${TMPDIR_TEST}/ddtrace/internal/datadog/profiling/run_clang_tidy.sh"
}

# Mock git: rev-parse returns TMPDIR_TEST as repo root; pre-push stdin drives diff output.
setup_git_mock() {
    changed_files="$1"
    cat > "${TMPDIR_TEST}/git" << EOF
#!/usr/bin/env sh
case "\$*" in
    "rev-parse --show-toplevel")
        printf '%s\n' "${TMPDIR_TEST}" ;;
    "diff --name-only "*)
        printf '%s\n' ${changed_files} ;;
    "show --pretty=\"\" --name-only "*)
        printf '%s\n' ${changed_files} ;;
    "merge-base "*)
        printf 'base\n' ;;
    *)
        exec "$(command -v git)" "\$@" ;;
esac
EOF
    chmod +x "${TMPDIR_TEST}/git"
}

run_hook() {
    push_input="$1"
    skip_flag="${2:-}"
    # Use a pipe, not <<< (bash-only); invoke the hook via its bash shebang.
    printf '%s\n' "${push_input}" | env -i \
        PATH="${TMPDIR_TEST}:${PATH}" \
        HOME="${TMPDIR_TEST}" \
        SKIP_CLANG_TIDY_PROFILING="${skip_flag}" \
        DDTRACE_PREPUSH_CLANG_TIDY_STRICT="${DDTRACE_PREPUSH_CLANG_TIDY_STRICT:-}" \
        bash "${HOOK}"
}

PUSH_INPUT="refs/heads/x $(git rev-parse HEAD) refs/heads/main $(git rev-parse HEAD~1 2>/dev/null || git rev-parse HEAD)"
ZERO_SHA="0000000000000000000000000000000000000000"

# No profiling native changes: hook is a no-op.
setup_toolchain 18
setup_repo_layout
setup_git_mock "README.md"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
run_hook "${PUSH_INPUT}" > /dev/null
check "no profiling changes skips clang-tidy" "! [ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

# Profiling C++ change with prerequisites: hook runs analysis in skip-build mode.
setup_toolchain 18
setup_repo_layout
setup_git_mock "ddtrace/internal/datadog/profiling/stack/src/sampler.cpp"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
run_hook "${PUSH_INPUT}" > /dev/null
check "profiling native changes run clang-tidy" "[ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

# LLVM <18 is rejected (skip, not run).
setup_toolchain 14
setup_repo_layout
setup_git_mock "ddtrace/internal/datadog/profiling/stack/src/sampler.cpp"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
run_hook "${PUSH_INPUT}" > /dev/null
check "clang-tidy older than 18 is skipped" "! [ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

# Missing compile_commands.json skips without running analysis.
setup_toolchain 18
setup_repo_layout
rm -f "${TMPDIR_TEST}/ddtrace/internal/datadog/profiling/build/compile_commands.json"
setup_git_mock "ddtrace/internal/datadog/profiling/stack/src/sampler.cpp"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
run_hook "${PUSH_INPUT}" > /dev/null
check "missing compile_commands skips clang-tidy" "! [ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

# SKIP_CLANG_TIDY_PROFILING=1 bypasses the check.
setup_toolchain 18
setup_repo_layout
setup_git_mock "ddtrace/internal/datadog/profiling/stack/src/sampler.cpp"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
run_hook "${PUSH_INPUT}" 1 > /dev/null
check "SKIP_CLANG_TIDY_PROFILING bypasses clang-tidy" "! [ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

# merge-base failure on a new branch forces clang-tidy even without profiling paths in the diff.
setup_toolchain 18
setup_repo_layout
cat > "${TMPDIR_TEST}/git" << EOF
#!/usr/bin/env sh
case "\$*" in
    "rev-parse --show-toplevel")
        printf '%s\n' "${TMPDIR_TEST}" ;;
    "merge-base "*)
        exit 1 ;;
    "diff --name-only "*)
        printf 'README.md\n' ;;
    *)
        exec "$(command -v git)" "\$@" ;;
esac
EOF
chmod +x "${TMPDIR_TEST}/git"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
# New remote branch: remote_sha is ZERO
run_hook "refs/heads/x $(git rev-parse HEAD) refs/heads/main ${ZERO_SHA}" > /dev/null
check "merge-base failure forces clang-tidy" "[ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL}" = "0" ]
