"""PostToolUse hook: detect missing native extension and suggest rebuild.

When Bash output contains:
    ModuleNotFoundError: No module named 'ddtrace.internal.native._native'

it means the C/Rust extensions haven't been compiled. Surfaces the fix
immediately so the agent doesn't waste turns diagnosing it.
"""

import json
import sys

TRIGGER = "No module named 'ddtrace.internal.native._native'"

FIX = (
    "\n"
    "\u26a0\ufe0f  ddtrace native extensions not built.\n"
    "\n"
    "The C/Rust extensions are missing from the current environment.\n"
    "Rebuild them with:\n"
    "\n"
    "    hatch run clean:all\n"
    "\n"
    "This cleans stale build artifacts and recompiles extensions in a fresh\n"
    "Hatch environment. See docs/troubleshooting.rst for details.\n"
)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    if data.get("tool_name") != "Bash":
        return

    output = ""
    tool_response = data.get("tool_response") or {}
    if isinstance(tool_response, dict):
        output = (
            tool_response.get("output", "")
            or tool_response.get("content", "")
            or ""
        )
    elif isinstance(tool_response, str):
        output = tool_response

    if TRIGGER in output:
        print(
            json.dumps({
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "suppressOutput": False,
                    "additionalContext": FIX,
                }
            })
        )


if __name__ == "__main__":
    main()
