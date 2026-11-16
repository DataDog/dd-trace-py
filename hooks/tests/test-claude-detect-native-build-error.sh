#!/usr/bin/env sh
# Tests for .claude/hooks/detect-native-build-error.py (Claude Code PostToolUse hook)
#
# Pipes PostToolUse payloads into the hook and checks when the rebuild advice is injected.
# Run with:  sh hooks/tests/test-claude-detect-native-build-error.sh

set -eu

SCRIPT="$(cd "$(dirname "$0")/../../.claude/hooks" && pwd)/detect-native-build-error.py"
PASS=0
FAIL=0
TRIGGER="ModuleNotFoundError: No module named 'ddtrace.internal.native._native'"

# Usage: run_hook TOOL SHAPE TEXT, where SHAPE is the tool_response field holding TEXT
# (stdout, stderr, output, content for a list of text blocks, or string for a bare string).
run_hook() {
    python3 -c '
import json, sys
tool, shape, text = sys.argv[1:]
if shape == "string":
    response = text
elif shape == "content":
    response = {"content": [{"type": "text", "text": text}]}
else:
    response = {shape: text}
print(json.dumps({"tool_name": tool, "tool_response": response}))
' "$1" "$2" "$3" | python3 "$SCRIPT"
}

expect_advice() {
    name="$1"; output="$2"
    if printf '%s' "$output" | grep -q '"additionalContext"' \
        && printf '%s' "$output" | grep -q 'pip install -e \.' \
        && printf '%s' "$output" | grep -q 'scripts/run-tests -r --venv'; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name  (output: ${output:-<none>})"
        FAIL=$((FAIL + 1))
    fi
}

expect_silent() {
    name="$1"; output="$2"
    if [ -z "$output" ]; then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name  (output: $output)"
        FAIL=$((FAIL + 1))
    fi
}

expect_advice "stderr field" "$(run_hook Bash stderr "Traceback (most recent call last):
$TRIGGER")"
expect_advice "stdout field" "$(run_hook Bash stdout "$TRIGGER")"
expect_advice "output field" "$(run_hook Bash output "$TRIGGER")"
expect_advice "content blocks" "$(run_hook Bash content "$TRIGGER")"
expect_advice "string response" "$(run_hook Bash string "$TRIGGER")"
expect_silent "unrelated import error" "$(run_hook Bash stderr "ModuleNotFoundError: No module named 'foo'")"
expect_silent "non-Bash tool" "$(run_hook Read output "$TRIGGER")"
expect_silent "invalid JSON" "$(printf 'not json' | python3 "$SCRIPT")"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
