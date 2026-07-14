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

RUN_SCRIPT="${TMPDIR_TEST}/run_clang_tidy.sh"
PROFILING_DIR="${TMPDIR_TEST}/ddtrace/internal/datadog/profiling"
mkdir -p "${PROFILING_DIR}"
printf '#!/usr/bin/env sh\necho run_clang_tidy >> "%s"\n' "${TMPDIR_TEST}/clang-tidy-calls.txt" > "${RUN_SCRIPT}"
chmod +x "${RUN_SCRIPT}"

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
  env -i \
      PATH="${TMPDIR_TEST}:${PATH}" \
      HOME="${TMPDIR_TEST}" \
      SKIP_CLANG_TIDY_PROFILING="${SKIP_CLANG_TIDY_PROFILING:-}" \
      DDTRACE_PREPUSH_CLANG_TIDY_STRICT="${DDTRACE_PREPUSH_CLANG_TIDY_STRICT:-}" \
      sh "${HOOK}" <<< "${push_input}"
}

# Provide fake toolchain binaries on PATH.
for cmd in cmake jq clang-tidy run-clang-tidy; do
    printf '#!/usr/bin/env sh\nexit 0\n' > "${TMPDIR_TEST}/${cmd}"
    chmod +x "${TMPDIR_TEST}/${cmd}"
done

# Point the hook at our stub run_clang_tidy.sh via repo layout under TMPDIR_TEST.
cat > "${TMPDIR_TEST}/ddtrace/internal/datadog/profiling/run_clang_tidy.sh" << EOF
#!/usr/bin/env sh
echo run_clang_tidy >> "${TMPDIR_TEST}/clang-tidy-calls.txt"
exit 0
EOF
chmod +x "${TMPDIR_TEST}/ddtrace/internal/datadog/profiling/run_clang_tidy.sh"

# No profiling native changes: hook is a no-op.
setup_git_mock "README.md"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
run_hook "refs/heads/x $(git rev-parse HEAD) refs/heads/main $(git rev-parse HEAD~1 2>/dev/null || git rev-parse HEAD)" > /dev/null
check "no profiling changes skips clang-tidy" "! [ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

# Profiling C++ change: hook runs clang-tidy script.
setup_git_mock "ddtrace/internal/datadog/profiling/stack/src/sampler.cpp"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
run_hook "refs/heads/x $(git rev-parse HEAD) refs/heads/main $(git rev-parse HEAD~1 2>/dev/null || git rev-parse HEAD)" > /dev/null
check "profiling native changes run clang-tidy" "[ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

# SKIP_CLANG_TIDY_PROFILING=1 bypasses the check.
setup_git_mock "ddtrace/internal/datadog/profiling/stack/src/sampler.cpp"
: > "${TMPDIR_TEST}/clang-tidy-calls.txt"
SKIP_CLANG_TIDY_PROFILING=1 run_hook "refs/heads/x $(git rev-parse HEAD) refs/heads/main $(git rev-parse HEAD~1 2>/dev/null || git rev-parse HEAD)" > /dev/null
check "SKIP_CLANG_TIDY_PROFILING bypasses clang-tidy" "! [ -s '${TMPDIR_TEST}/clang-tidy-calls.txt' ]"

echo ""
echo "Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL}" = "0" ]
