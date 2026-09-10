#!/usr/bin/env bash
# Runs "riot -v run -s --pass-env wait -- $@" and retries up to twice,
# but ONLY if the failure was caused by a transient pip network error.
# Failures from the wait scripts themselves (e.g. service unavailable) are
# not retried, so a long-running readiness check like check_opensearch does
# not consume the full job timeout multiple times.

set -euo pipefail

# Patterns that indicate a pip install failure rather than a service failure.
_PIP_ERR_PATTERN='pip|ConnectionReset|HTTPError|ChunkedEncoding|ProtocolError|Could not install'

_try_wait() {
    local _tmpfile _rc=0
    _tmpfile=$(mktemp)
    riot -v run -s --pass-env wait -- "$@" >"$_tmpfile" 2>&1 || _rc=$?
    cat "$_tmpfile"
    if [ "$_rc" -ne 0 ] && grep -qE "$_PIP_ERR_PATTERN" "$_tmpfile"; then
        rm -f "$_tmpfile"
        return 2  # pip error -- caller should retry
    fi
    rm -f "$_tmpfile"
    return "$_rc"
}

_try_wait "$@" && exit 0
_rc=$?
[ "$_rc" -eq 2 ] || exit "$_rc"

# First retry
_try_wait "$@" && exit 0
_rc=$?
[ "$_rc" -eq 2 ] || exit "$_rc"

# Second (final) retry -- let it fail naturally
exec riot -v run -s --pass-env wait -- "$@"
