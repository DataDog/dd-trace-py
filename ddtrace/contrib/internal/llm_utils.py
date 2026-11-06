"""Helpers shared by LLM integrations and LLM Observability that do not depend on either product."""

import json
from typing import Any

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _unserializable_default_repr(obj: Any) -> Any:
    try:
        # Pydantic v2
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            return obj.model_dump(mode="json")
        # Pydantic v1
        if hasattr(obj, "__fields__") and hasattr(obj, "dict") and callable(obj.dict):
            return obj.dict()
        return str(obj)
    except Exception:
        log.warning("I/O object is neither JSON serializable nor string-able. Defaulting to placeholder value instead.")
        return f"[Unserializable object: {repr(obj)}]"


# Returns None when serialization fails. Typed Any to keep the unchecked contract its
# existing callers were written against.
def safe_json(obj: Any, ensure_ascii: bool = True) -> Any:
    if isinstance(obj, str):
        return obj
    try:
        # Pydantic v2
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            obj = obj.model_dump(mode="json")
        # Pydantic v1
        elif hasattr(obj, "__fields__") and hasattr(obj, "dict") and callable(obj.dict):
            obj = obj.dict()
        return json.dumps(
            obj, ensure_ascii=ensure_ascii, sort_keys=True, skipkeys=True, default=_unserializable_default_repr
        )
    except Exception:
        log.error("Failed to serialize object to JSON.", exc_info=True)
        return None
