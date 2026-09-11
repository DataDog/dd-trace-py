#!/usr/bin/env python3
"""PreToolUse hook: nudge toward a skill when editing certain paths.

Add (path_regex, message) pairs to RULES to cover new areas as they come up.
"""

import json
import re
import sys


RULES = [
    (
        re.compile(r"releasenotes/notes/[^/]+\.ya?ml$"),
        "Use the `releasenote` skill for this Reno fragment instead of hand-writing it — see docs/releasenotes.rst.",
    ),
]


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    if data.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return

    path = (data.get("tool_input") or {}).get("file_path", "")
    messages = [msg for pattern, msg in RULES if pattern.search(path)]
    if not messages:
        return

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "suppressOutput": False,
                    "additionalContext": "\n".join(messages),
                }
            }
        )
    )


if __name__ == "__main__":
    main()
