"""PostToolUse / PostToolUseFailure hook: detect missing native extension and suggest rebuild.

When any Bash tool output contains:
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


def _extract_output(tool_response) -> str:
    """Return all text from a tool_response regardless of field shape."""
    if isinstance(tool_response, str):
        return tool_response
    if not isinstance(tool_response, dict):
        return ""
    # Claude Code Bash results use various field names across versions
    parts = []
    for key in ("output", "content", "stdout", "stderr"):
        val = tool_response.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
        elif isinstance(val, list):
            # content blocks list
            for block in val:
                if isinstance(block, dict):
                    parts.append(block.get("text", ""))
    return "\n".join(parts)


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    if data.get("tool_name") != "Bash":
        return

    output = _extract_output(data.get("tool_response") or {})

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
