from __future__ import annotations

from io import IOBase
from typing import Any
from typing import Mapping
from typing import Sequence
from typing import Union

JSONScalar = Union[str, int, float, bool, None]
"""A scalar JSON-like value.

This includes primitive types that can appear in JSON:
string, number, boolean, or null.
"""

JSON = Union[JSONScalar, Sequence[Any], Mapping[str, Any]]
"""A JSON-like data structure.

This covers scalars, sequences (e.g. lists, tuples), and mappings (e.g.
dictionaries with string keys). Values inside may be untyped (`Any`) rather
than recursively constrained to `JSON` for flexibility.
"""

JSONData = Union[str, IOBase, JSON]
"""Input representing JSON content.

Accepts:
- a JSON-like object (`JSON`),
- a raw JSON string,
- or a file-like object containing JSON data.
"""
