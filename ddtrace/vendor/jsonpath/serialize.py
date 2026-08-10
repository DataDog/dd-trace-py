"""Helper functions for serializing compiled JSONPath queries."""

import json


def canonical_string(value: str) -> str:
    """Return _value_ as a canonically formatted string literal."""
    single_quoted = (
        json.dumps(value, ensure_ascii=False)[1:-1]
        .replace('\\"', '"')
        .replace("'", "\\'")
    )
    return f"'{single_quoted}'"
