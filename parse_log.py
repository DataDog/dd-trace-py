#!/usr/bin/env python3
"""Parse log.log file containing stack traces."""

from collections import Counter
from dataclasses import dataclass
import fnmatch
import re
from typing import List
from typing import Tuple


@dataclass(frozen=True)
class StackFrame:
    """Represents a single frame in a stack trace."""

    function_name: str
    module_path: str
    line_number: int

    def __str__(self) -> str:
        return f"{self.function_name} {self.module_path}:{self.line_number}"


@dataclass(frozen=True)
class StackTrace:
    """Represents a complete stack trace."""

    frames: Tuple[StackFrame, ...]

    def __str__(self) -> str:
        return "\n".join(str(frame) for frame in self.frames)


def parse_log_file(file_path: str) -> List[StackTrace]:
    """
    Parse a log file containing stack traces.

    Args:
        file_path: Path to the log file

    Returns:
        List of StackTrace objects
    """
    stack_traces: List[StackTrace] = []
    current_frames: List[StackFrame] = []
    in_stack: bool = False

    # Pattern to match: "Pushing frame: <function> <module> <line>"
    frame_pattern = re.compile(r"Pushing frame:\s+(.+?)\s+(.+?)\s+(\d+)")

    with open(file_path, "r") as f:
        for line in f:
            line = line.strip()

            if line == "-- Stack begin":
                if in_stack:
                    # Handle malformed log (stack begin without end)
                    stack_traces.append(StackTrace(frames=tuple(current_frames.copy())))
                    current_frames.clear()
                in_stack = True
                current_frames.clear()
            elif line == "-- Stack end":
                if in_stack:
                    stack_traces.append(StackTrace(frames=tuple(current_frames.copy())))
                    current_frames.clear()
                    in_stack = False
                else:
                    # Handle malformed log (stack end without begin)
                    pass
            elif in_stack and line.startswith("Pushing frame:"):
                match = frame_pattern.match(line)
                if match:
                    function_name = match.group(1)
                    module_path = match.group(2)
                    line_number = int(match.group(3))
                    current_frames.append(
                        StackFrame(function_name=function_name, module_path=module_path, line_number=line_number)
                    )

    # Handle case where file ends without "-- Stack end"
    if in_stack and current_frames:
        stack_traces.append(StackTrace(frames=tuple(current_frames)))

    return stack_traces


def find_stacks_with_subsequence(
    stacks: List[StackTrace], function_names: List[str], case_sensitive: bool = False
) -> List[StackTrace]:
    """
    Find stacks that contain the given function names/patterns in the specified order.

    The function names must appear in the stack in the same order, but other
    frames may appear between them. Supports glob-style patterns (e.g., "Task*"
    to match any function starting with "Task").

    Args:
        stacks: List of StackTrace objects to search
        function_names: List of function names/patterns to search for (in order)
                       Supports wildcards: * (any chars), ? (single char)
        case_sensitive: Whether to perform case-sensitive matching

    Returns:
        List of StackTrace objects that contain the subsequence
    """
    if not function_names:
        return []

    patterns = function_names if case_sensitive else [name.lower() for name in function_names]

    matching_stacks: List[StackTrace] = []

    for stack in stacks:
        frame_names = [frame.function_name for frame in stack.frames]
        if not case_sensitive:
            frame_names = [name.lower() for name in frame_names]

        # Check if patterns match in order in frame_names
        pattern_idx = 0
        for frame_name in frame_names:
            if pattern_idx < len(patterns) and fnmatch.fnmatch(frame_name, patterns[pattern_idx]):
                pattern_idx += 1
                if pattern_idx == len(patterns):
                    # Found all patterns in order
                    matching_stacks.append(stack)
                    break

    return matching_stacks


def main() -> None:
    """Main entry point."""
    import sys

    file_path = sys.argv[1] if len(sys.argv) > 1 else "log.log"

    print(f"Parsing {file_path}...")
    stack_traces = parse_log_file(file_path)

    print(f"\nFound {len(stack_traces)} total stack traces\n")

    # Count occurrences of each unique stack
    stack_counter: Counter[StackTrace] = Counter(stack_traces)

    # Print statistics
    total_frames = sum(len(st.frames) * count for st, count in stack_counter.items())
    empty_stacks = sum(count for st, count in stack_counter.items() if len(st.frames) == 0)
    unique_stacks = len(stack_counter)
    unique_non_empty = sum(1 for st in stack_counter if len(st.frames) > 0)

    print(f"Total stack traces: {len(stack_traces)}")
    print(f"Unique stack traces: {unique_stacks}")
    print(f"Total frames: {total_frames}")
    print(f"Empty stacks: {empty_stacks}")
    print(f"Unique non-empty stacks: {unique_non_empty}")

    # Print stacks sorted by occurrence count (most frequent first)
    print("\n--- Unique stack traces (sorted by occurrence count) ---\n")
    for i, (stack, count) in enumerate(stack_counter.most_common(), 1):
        print(f"Stack trace #{i} (occurs {count} time{'s' if count > 1 else ''}, {len(stack.frames)} frames):")
        if len(stack.frames) > 0:
            print(stack)
        else:
            print("  (empty stack)")
        print()

    print("=== Finding stacks with subsequence ===")
    result = find_stacks_with_subsequence(stack_traces, ["Fetch*", "matrix_coro"])
    print(f"Found {len(result)} stacks with subsequence")
    for stack in result:
        print(stack)
        print("--------------------------------")


if __name__ == "__main__":
    main()
