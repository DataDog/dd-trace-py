#!/usr/bin/env bash
# Crash Python on purpose and check that generate-core-backtraces.sh collects
# the core, symbolizes it, adds the Python stack and names the running test,
# and drops the core of a crash outside Python. This fails when CI stops
# producing core dumps, for example when the runners change
# kernel.core_pattern, the testrunner image loses a writable core directory, or
# before_script drops ulimit -c unlimited (so don't raise the limit here).

set -euo pipefail

fail() {
    echo "coredump smoke test failed: $*" >&2
    exit 1
}

rm -rf core-dumps test-results

# A test that starts and never finishes, the way the root conftest.py records it.
mkdir -p test-results
printf 'start\t%s\tsmoke::test_crash\n' "$(date +%s)" > test-results/test-timeline.smoke.tsv

# Crash through a console-script wrapper, the way pytest runs in CI, so the
# backtrace script has to follow the shebang to find the interpreter.
python=$(python3 -c "import sys; print(sys.executable)")
wrapper="$(mktemp -d)/crash-wrapper"
printf '#!%s\nimport ctypes\nctypes.string_at(0)\n' "$python" > "$wrapper"
chmod +x "$wrapper"

rc=0
"$wrapper" || rc=$?
[ "$rc" -eq 139 ] || fail "expected the wrapper to die with SIGSEGV (exit 139), got exit $rc"

# A crash outside Python, whose core the backtrace script must drop.
sleep 60 &
kill -SEGV $!
rc=0
wait $! || rc=$?
[ "$rc" -eq 139 ] || fail "expected sleep to die with SIGSEGV (exit 139), got exit $rc"

bash .gitlab/scripts/generate-core-backtraces.sh

shopt -s nullglob
backtraces=(core-dumps/*.bt.txt)
[ "${#backtraces[@]}" -eq 1 ] || fail "expected one backtrace in core-dumps/, for the Python crash, got ${#backtraces[@]}"
cores=(core-dumps/*)
[ "${#cores[@]}" -eq 2 ] || fail "expected only the Python core and its backtrace in core-dumps/, got: ${cores[*]}"
backtrace=${backtraces[0]}

# string_at is the C function in _ctypes that dereferences the null pointer.
grep -q "string_at" "$backtrace" || fail "the backtrace does not contain the crashing frame string_at"
# py-bt prints the Python frame that called it.
grep -q "crash-wrapper\", line 3" "$backtrace" || fail "the backtrace does not contain the Python stack"
grep -q "smoke::test_crash (did not finish)" "$backtrace" || fail "the backtrace does not name the running test"
