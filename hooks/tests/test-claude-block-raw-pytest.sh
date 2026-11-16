#!/usr/bin/env sh
# Tests for .claude/hooks/block-raw-pytest.py (Claude Code PreToolUse hook)
#
# Pipes PreToolUse payloads into the hook and checks which Bash commands are denied.
# Run with:  sh hooks/tests/test-claude-block-raw-pytest.sh

set -eu

SCRIPT="$(cd "$(dirname "$0")/../../.claude/hooks" && pwd)/block-raw-pytest.py"
PASS=0
FAIL=0

run_hook() {
    python3 -c 'import json, sys; print(json.dumps({"tool_name": sys.argv[1], "tool_input": {"command": sys.argv[2]}}))' \
        "$1" "$2" | python3 "$SCRIPT"
}

expect_deny() {
    output=$(run_hook Bash "$1")
    if printf '%s' "$output" | grep -q '"permissionDecision": "deny"'; then
        echo "PASS: denied: $1"
        PASS=$((PASS + 1))
    else
        echo "FAIL: expected deny: $1  (output: ${output:-<none>})"
        FAIL=$((FAIL + 1))
    fi
}

expect_allow() {
    tool="${2:-Bash}"
    output=$(run_hook "$tool" "$1")
    if [ -z "$output" ]; then
        echo "PASS: allowed ($tool): $1"
        PASS=$((PASS + 1))
    else
        echo "FAIL: expected allow ($tool): $1  (output: $output)"
        FAIL=$((FAIL + 1))
    fi
}

expect_deny "pytest tests/profiling/test_profiler.py"
expect_deny "py.test tests/profiling"
expect_deny "python -m pytest tests/profiling -q"
expect_deny "python3.12 -m pytest -x tests/profiling"
expect_deny "/usr/bin/python3 -mpytest tests"
expect_deny ".venv/bin/pytest tests/profiling"
expect_deny "DD_TRACE_AGENT_URL=http://localhost:9126 python -m pytest tests/contrib/starlette -q"
expect_deny "env -u ANTHROPIC_API_KEY DD_SERVICE=x python -m pytest tests/contrib/anthropic"
expect_deny "cd tests && pytest profiling"
expect_deny "uv run --with pytest-xdist pytest tests/profiling"
expect_deny "timeout 600 pytest tests/profiling"
expect_deny "bash -c 'pytest tests/profiling'"
expect_deny "echo start
pytest tests/profiling"
expect_deny "pytest tests/profiling 2>&1 | tail -20"

expect_allow "scripts/run-tests --list ddtrace/profiling/profiler.py"
expect_allow "scripts/run-tests --venv abc123 -- -k test_profiler"
expect_allow ".agents/skills/profile/austin-docker --venv abc123 --name t -- python -m pytest -p no:randomly tests/profiling"
expect_allow "grep -rn pytest tests/profiling"
expect_allow "pip install pytest"
expect_allow "git commit -m 'run pytest via scripts/run-tests'"
expect_allow "uv run --with pytest python scripts/check.py"
expect_allow "python -c 'import pytest'"
expect_allow "ls .venv/bin/pytest"
expect_allow "echo 'unbalanced"
expect_allow "pytest tests" Read

output=$(printf 'not json' | python3 "$SCRIPT")
if [ -z "$output" ]; then
    echo "PASS: invalid JSON is ignored"
    PASS=$((PASS + 1))
else
    echo "FAIL: invalid JSON produced output: $output"
    FAIL=$((FAIL + 1))
fi

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
