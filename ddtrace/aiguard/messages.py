"""Shared utilities for AI Guard message formatting across integrations."""

import json
from typing import Any


def try_format_json(value: Any) -> str:
    """Format a value as a JSON string, falling back to str() on failure.

    :param value: The value to serialize.
    :returns: JSON string, ``str(value)`` on serialization failure, or ``""`` if None.
    """
    if value is None:
        return ""
    try:
        return json.dumps(value)
    except Exception:
        return str(value)
