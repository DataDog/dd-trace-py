#!/usr/bin/env python3
"""
Validate that once a Task name is removed from the string table,
it never appears again in the log.
"""

import re
import sys
from typing import Set


def validate_task_removal(log_file: str) -> bool:
    """
    Parse log file and ensure removed Task names never appear again.

    Returns True if validation passes, False otherwise.
    """
    removed_tasks: Set[str] = set()
    violations: list[tuple[int, str, str]] = []

    with open(log_file, "r") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()

            # Match "pushing <name>" or "removing <name>"
            push_match = re.match(r"pushing\s+(.+)", line)
            remove_match = re.match(r"removing\s+(.+)", line)

            if push_match:
                name = push_match.group(1)
                if name in removed_tasks:
                    violations.append((line_num, "pushing", name))

            elif remove_match:
                name = remove_match.group(1)
                if name in removed_tasks:
                    violations.append((line_num, "removing", name))
                removed_tasks.add(name)

    # Report results
    if violations:
        print(f"❌ VALIDATION FAILED: Found {len(violations)} violation(s)")
        print("\nTask names that appeared after removal:")
        for line_num, operation, name in violations:
            print(f"  Line {line_num}: {operation} {name}")
        return False
    else:
        print(f"✅ VALIDATION PASSED: All {len(removed_tasks)} removed tasks never appear again")
        print(f"   Removed tasks: {sorted(removed_tasks)}")
        return True


if __name__ == "__main__":
    log_file = sys.argv[1] if len(sys.argv) > 1 else "log6.log"
    success = validate_task_removal(log_file)
    sys.exit(0 if success else 1)
