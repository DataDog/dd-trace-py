from typing import Any
from typing import Optional

from ddtrace.internal.utils.formats import _get_attr


DEFAULT_TOOL_ERROR_MESSAGE = "tool resulted in an error"


def is_tool_error(result: Any) -> bool:
    return bool(_get_attr(result, "isError", None) or _get_attr(result, "is_error", None))


def get_tool_error_message(result: Any) -> Optional[str]:
    content = _get_attr(result, "content", None) or []
    if not isinstance(content, list):
        return None
    for item in content:
        if _get_attr(item, "type", None) == "text":
            text = _get_attr(item, "text", None)
            if text:
                return str(text)
    return None
