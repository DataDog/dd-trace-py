import json
import re
from io import IOBase
from typing import Any

_RE_PROBABLY_MALFORMED = re.compile(r"[\{\}\[\]]")


def load_data(data: object) -> Any:
    if isinstance(data, str):
        try:
            return json.loads(data)
        except json.JSONDecodeError:
            # Overly simple way to detect a malformed JSON document vs a
            # top-level string only document
            if _RE_PROBABLY_MALFORMED.search(data):
                raise
            return data
    if isinstance(data, IOBase):
        return json.loads(data.read())
    return data
