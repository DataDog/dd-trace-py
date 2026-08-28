"""Sensitive data redaction for AI Guard evaluations.

The service returns the fully redacted string per location path, so applying it is a verbatim
overwrite. Never raises: a malformed response degrades to a skip that leaves the message untouched.
"""

from collections.abc import Mapping
from collections.abc import MutableMapping
from copy import deepcopy
import re
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.aiguard._types import Message
import ddtrace.internal.logger as ddlogger


logger = ddlogger.get_logger(__name__)

# One segment of a location path: a field name plus an optional non-negative list index. The whole
# segment must match; a partial match is rejected. Shared by every tracer, see the AI Guard redaction RFC.
_SEGMENT_RE = re.compile(r"^(?P<name>[A-Za-z0-9_]+)(?:\[(?P<index>[0-9]+)\])?\Z")

# Terminal field names a replacement may be written to. Everything else resolves read-only, so a path
# pointing at an image locator, a role or a tool name can never overwrite it.
_REDACTABLE_TERMINALS = frozenset({"content", "text", "arguments"})

# Marks a path the backend sent conflicting replacements for: it is skipped rather than guessed.
_SKIP = object()

Segment = tuple[str, Optional[int]]
# What a replacement can be written into: everything else resolves read-only.
Writable = Union[MutableMapping[str, Any], list[Any]]


def _split_segments(path: str) -> Optional[list[Segment]]:
    """Split a location path into (name, index) segments, or None if any segment is malformed."""
    segments: list[Segment] = []
    for raw in path.split("."):
        match = _SEGMENT_RE.match(raw)
        if match is None:
            return None
        index = match.group("index")
        segments.append((match.group("name"), int(index) if index is not None else None))
    return segments


def _get_field(node: object, name: str) -> object:
    """Read a field off a mapping or an object, None when absent."""
    if isinstance(node, Mapping):
        return node.get(name)
    return getattr(node, name, None)


def _step(node: object, name: str, index: Optional[int]) -> object:
    """Resolve one path segment, None whenever the field or the list index does not exist."""
    value = _get_field(node, name)
    if value is None or index is None:
        return value
    # Strictly a list: a generic subscript check would happily index into a string.
    if not isinstance(value, list) or index >= len(value):
        return None
    return value[index]


def _resolve_writable_string(root: dict[str, Any], path: str) -> Optional[tuple[Writable, Any]]:
    """Resolve path to the (container, key) of a writable string, or None to skip it fail-safe.

    The key type follows the container, a correlation the type checker cannot express: a list
    always comes back with its int index, a mapping with its str field name.
    """
    segments = _split_segments(path)
    if not segments:
        return None

    name, index = segments[-1]
    if name not in _REDACTABLE_TERMINALS:
        return None

    node: object = root
    for parent_name, parent_index in segments[:-1]:
        node = _step(node, parent_name, parent_index)
        if node is None:
            return None

    value = _get_field(node, name)
    container: Writable
    key: Union[str, int]
    if index is None:
        # Writing back needs item assignment; SDK objects resolved through getattr are left alone.
        if not isinstance(node, MutableMapping):
            return None
        container, key, target = node, name, value
    else:
        if not isinstance(value, list) or index >= len(value):
            return None
        container, key, target = value, index, value[index]

    if not isinstance(target, str):
        return None
    return container, key


def _set_string_at_path(root: dict[str, Any], path: str, value: str) -> bool:
    """Overwrite the string at path, returning whether it was written."""
    resolved = _resolve_writable_string(root, path)
    if resolved is None:
        return False
    container, key = resolved
    container[key] = value
    return True


def _collect_replacements(replacements: object) -> dict[str, object]:
    """Collect one authoritative replacement per path, or _SKIP when the backend contradicts itself."""
    if not isinstance(replacements, list):
        return {}

    by_path: dict[str, object] = {}
    for entry in replacements:
        if not isinstance(entry, Mapping):
            continue
        path = entry.get("path")
        replacement = entry.get("replacement")
        # An empty replacement is valid: it is the customer's "remove" placeholder. A non-string one
        # is not, and would break serialization further down the line.
        if not path or not isinstance(path, str) or not isinstance(replacement, str):
            continue
        previous = by_path.get(path)
        if previous is not None and previous != replacement:
            by_path[path] = _SKIP
            continue
        by_path[path] = replacement
    return by_path


def redact_messages(messages: list[Message], replacements: object) -> list[Message]:
    """Apply the replacements to the messages and return the redacted list.

    Copy-on-write: the caller's messages are never mutated, and the very same list object is returned
    when nothing was applied, so callers can use identity to detect whether anything changed.
    """
    try:
        # Absent or empty is the service saying there is nothing to redact, not a malformed response.
        if not replacements:
            return messages

        by_path = _collect_replacements(replacements)
        if not by_path:
            return messages

        result = deepcopy(messages)
        root = {"messages": result}
        applied = 0
        for path, replacement in by_path.items():
            # _SKIP is not a str, so a contradicted path is skipped along with any path that is
            # missing, malformed or not pointing at a redactable string.
            if isinstance(replacement, str) and _set_string_at_path(root, path, replacement):
                applied += 1

        return result if applied else messages
    except Exception:
        logger.debug("AI Guard redaction failed", exc_info=True)
        return messages
