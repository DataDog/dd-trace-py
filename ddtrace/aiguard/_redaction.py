"""Sensitive data redaction for AI Guard evaluations.

The service returns the fully redacted string per location path, so applying it is a verbatim
overwrite. Never raises: a malformed response degrades to a skip that leaves the message untouched.
Every skip is counted so the caller can report it, see the redaction errors addendum of the RFC.
"""

from collections.abc import Mapping
from collections.abc import MutableMapping
from copy import deepcopy
import re
from typing import Any
from typing import NamedTuple
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

Segment = tuple[str, Optional[int]]
# What a replacement can be written into: everything else resolves read-only.
Writable = Union[MutableMapping[str, Any], list[Any]]


class RedactionResult(NamedTuple):
    """Messages to use downstream, plus how many replacements could not be applied."""

    messages: list[Message]
    errors: int


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


def _collect_replacements(replacements: object) -> tuple[dict[str, str], int]:
    """Collect one authoritative replacement per path, plus the number of entries that are unusable.

    A path the backend sends contradicting values for is dropped rather than guessed, and counted
    once however many entries disagree. Identical duplicates agree, so they are not errors.
    """
    if not isinstance(replacements, list):
        # Not even an array: nothing can be applied, so the whole payload counts as one error.
        return {}, 1

    errors = 0
    by_path: dict[str, str] = {}
    conflicting: set[str] = set()
    for entry in replacements:
        if not isinstance(entry, Mapping):
            errors += 1
            continue
        path = entry.get("path")
        replacement = entry.get("replacement")
        # An empty replacement is valid: it is the customer's "remove" placeholder. A non-string one
        # is not, and would break serialization further down the line.
        if not path or not isinstance(path, str) or not isinstance(replacement, str):
            errors += 1
            continue
        previous = by_path.get(path)
        if previous is not None and previous != replacement:
            conflicting.add(path)
        by_path[path] = replacement

    for path in conflicting:
        del by_path[path]
    return by_path, errors + len(conflicting)


def redact_messages(messages: list[Message], replacements: object) -> RedactionResult:
    """Apply the replacements to the messages and return them along with the redaction error count.

    Copy-on-write: the caller's messages are never mutated, and the very same list object is returned
    when nothing was applied, so callers can use identity to detect whether anything changed.
    """
    # An absent field is the service saying there is nothing to redact. Anything else that is
    # present, an empty array included, is classified by _collect_replacements.
    if replacements is None:
        return RedactionResult(messages, 0)

    errors = 0
    by_path: dict[str, str] = {}
    try:
        by_path, errors = _collect_replacements(replacements)
        if not by_path:
            return RedactionResult(messages, errors)

        result = deepcopy(messages)
        root = {"messages": result}
        applied = 0
        skipped = 0
        for path, replacement in by_path.items():
            # A path that is missing, malformed or not pointing at a redactable string is skipped
            # fail-safe, and reported rather than silently dropped.
            if _set_string_at_path(root, path, replacement):
                applied += 1
            else:
                skipped += 1

        return RedactionResult(result if applied else messages, errors + skipped)
    except Exception:
        logger.debug("AI Guard redaction failed", exc_info=True)
        # Nothing is delivered when the pass dies, so every collected path is one more replacement
        # we failed to apply, on top of the entries that were already unusable.
        return RedactionResult(messages, max(errors + len(by_path), 1))
