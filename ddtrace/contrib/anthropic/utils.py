from typing import Any

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _get_attr(o: Any, attr: str, default: Any):
    # Since our response may be a dict or object, convenience method
    if isinstance(o, dict):
        return o.get(attr, default)
    else:
        return getattr(o, attr, default)
