"""PostToolUse / PostToolUseFailure hook: detect missing native extension and suggest rebuild.

When any Bash tool output contains:
    ModuleNotFoundError: No module named 'ddtrace.internal.native._native'

it means the C/Rust extensions haven't been compiled. Surfaces the fix
immediately so the agent doesn't waste turns diagnosing it.
"""

import json
import sys


TRIGGER = "No module named 'ddtrace.internal.native._native'"

FIX = """
\u26a0\ufe0f  ddtrace native extensions not built (C/Rust extensions missing).

The fix depends on how you are running ddtrace:

  Direct Python / pip install
  ---------------------------
  Recompile the extensions in-place:

      pip install -e .

  Riot (test runner)
  ------------------
  If you are using the -s flag (skip base install), that is why extensions are
  missing. Drop -s on your next run so riot rebuilds them:

      riot -v run -p <python_version> <suite_name>

  Docker / scripts/ddtest
  -----------------------
  Clean build artifacts on the host first, then remount:

      scripts/clean

See docs/troubleshooting.rst for full details.
"""


def _extract_output(tool_response) -> str:
    """Return all text from a tool_response regardless of field shape."""
    if isinstance(tool_response, str):
        return tool_response
    if not isinstance(tool_response, dict):
        return ""
    parts = []
    for key in ("output", "content", "stdout", "stderr"):
        val = tool_response.get(key)
        if isinstance(val, str) and val:
            parts.append(val)
        elif isinstance(val, list):
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
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "suppressOutput": False,
                        "additionalContext": FIX,
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
