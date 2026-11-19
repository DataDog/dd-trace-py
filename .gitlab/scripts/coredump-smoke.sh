#!/usr/bin/env bash
# Crash Python on purpose and check that generate-core-backtraces.sh collects
# the core and symbolizes it. This fails when CI stops producing core dumps,
# for example when the runners change kernel.core_pattern, the testrunner image
# loses a writable core directory, or before_script drops ulimit -c unlimited
# (so don't raise the limit here).

set -euo pipefail

fail() {
    echo "coredump smoke test failed: $*" >&2
    exit 1
}

rm -rf core-dumps

# Crash through a console-script wrapper, the way pytest runs in CI, so the
# backtrace script has to follow the shebang to find the interpreter.
python=$(python3 -c "import sys; print(sys.executable)")
wrapper="$(mktemp -d)/crash-wrapper"
printf '#!%s\nimport ctypes\nctypes.string_at(0)\n' "$python" > "$wrapper"
chmod +x "$wrapper"

rc=0
"$wrapper" || rc=$?
[ "$rc" -eq 139 ] || fail "expected the wrapper to die with SIGSEGV (exit 139), got exit $rc"

bash .gitlab/scripts/generate-core-backtraces.sh

shopt -s nullglob
backtraces=(core-dumps/*.bt.txt)
[ "${#backtraces[@]}" -gt 0 ] || fail "no core dump was collected into core-dumps/"

# string_at is the C function in _ctypes that dereferences the null pointer.
grep -l "string_at" "${backtraces[@]}" || fail "no backtrace contains the crashing frame string_at"
