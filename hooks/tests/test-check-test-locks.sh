#!/usr/bin/env sh
# Tests for hooks/pre-commit/12-check-test-locks
#
# Stubs git and the lock checker (via CHECK_CMD) so no real lock inventory is needed.
# Run with:  sh hooks/tests/test-check-test-locks.sh

set -eu

SCRIPT="$(cd "$(dirname "$0")/../pre-commit" && pwd)/12-check-test-locks"
PASS=0
FAIL=0

TMPDIR_TEST=$(mktemp -d)
trap 'rm -rf "$TMPDIR_TEST"' EXIT

CHECK_CALLS="$TMPDIR_TEST/check-calls.txt"
SNAPSHOT_MARKER="$TMPDIR_TEST/ran-in-snapshot.txt"

mock_staged() {
    files="$1"
    cat > "$TMPDIR_TEST/git" << EOF
#!/usr/bin/env sh
case "\$*" in
    "diff --staged --name-only HEAD --diff-filter=ACMRD")
        printf '%s\n' $files ;;
    *)
        exec "$(command -v git)" "\$@" ;;
esac
EOF
    chmod +x "$TMPDIR_TEST/git"
}

setup_check_mock() {
    status="${1:-0}"
    cat > "$TMPDIR_TEST/check-mock" << EOF
#!/usr/bin/env sh
echo "\$*" >> "$CHECK_CALLS"
exit $status
EOF
    chmod +x "$TMPDIR_TEST/check-mock"
    : > "$CHECK_CALLS"
}

run_hook() {
    PATH="$TMPDIR_TEST:$PATH" CHECK_CMD="$TMPDIR_TEST/check-mock" sh "$SCRIPT" 2>&1
}

check() {
    name="$1"; condition="$2"
    if eval "$condition"; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name  (check calls: $(cat "$CHECK_CALLS" 2>/dev/null || echo '<none>'))"
        FAIL=$((FAIL + 1))
    fi
}

setup_check_mock 0

mock_staged "foo.py"
: > "$CHECK_CALLS"
output=$(run_hook)
check "unrelated file: check not called" "! [ -s '$CHECK_CALLS' ]"
check "unrelated file: skip message printed" "printf '%s' '$output' | grep -q 'skipped'"

mock_staged "tests/requirements_locks/abc123def456.txt"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "lock file: check runs" "grep -qF 'check' '$CHECK_CALLS'"

mock_staged "tests/flask/suitespec.yml"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "nested suitespec.yml: check runs" "grep -qF 'check' '$CHECK_CALLS'"

mock_staged "tests/suitespec.py"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "suitespec.py: check runs" "grep -qF 'check' '$CHECK_CALLS'"

mock_staged "scripts/test-requirements"
: > "$CHECK_CALLS"
run_hook > /dev/null
check "test-requirements script: check runs" "grep -qF 'check' '$CHECK_CALLS'"

setup_check_mock 2
mock_staged "tests/requirements_locks/deadbeef.txt"
: > "$CHECK_CALLS"
set +e
run_hook > /dev/null
hook_rc=$?
set -e
check "failed inventory: hook exits non-zero" "[ '$hook_rc' -ne 0 ]"

# Default CHECK_CMD path: materialize the index into a temp tree and run the
# checker from there (not the live worktree).
FIXTURE_SRC="$TMPDIR_TEST/fixture-src"
mkdir -p "$FIXTURE_SRC/scripts"
cat > "$FIXTURE_SRC/scripts/test-requirements" << EOF
#!/usr/bin/env sh
# Record that check ran from the snapshot tree, not the repo worktree.
printf '%s\n' "\$PWD" > "$SNAPSHOT_MARKER"
[ "\$1" = "check" ] || exit 2
exit 0
EOF
chmod +x "$FIXTURE_SRC/scripts/test-requirements"

cat > "$TMPDIR_TEST/git" << EOF
#!/usr/bin/env sh
case "\$1" in
    diff)
        printf '%s\n' "tests/requirements_locks/abc.txt"
        ;;
    ls-files)
        printf '%s\0' "scripts/test-requirements"
        ;;
    checkout-index)
        prefix=""
        while [ \$# -gt 0 ]; do
            case "\$1" in
                --prefix=*)
                    prefix=\${1#--prefix=}
                    ;;
            esac
            shift
        done
        [ -n "\$prefix" ] || exit 3
        mkdir -p "\${prefix}scripts"
        cp "$FIXTURE_SRC/scripts/test-requirements" "\${prefix}scripts/test-requirements"
        chmod +x "\${prefix}scripts/test-requirements"
        ;;
    *)
        exit 4
        ;;
esac
EOF
chmod +x "$TMPDIR_TEST/git"
: > "$SNAPSHOT_MARKER"
set +e
PATH="$TMPDIR_TEST:$PATH" sh "$SCRIPT" > /dev/null
snapshot_rc=$?
set -e
check "index snapshot: hook exits zero" "[ '$snapshot_rc' -eq 0 ]"
check "index snapshot: check ran inside snapshot" "[ -s '$SNAPSHOT_MARKER' ]"
check "index snapshot: check cwd is not the repo" \
    "! grep -qFx '$(pwd)' '$SNAPSHOT_MARKER' 2>/dev/null"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
