from typing import Any
from typing import Dict

from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def _get_attr(o: Any, attr: str, default: Any):
    # Since our response may be a dict or object, convenience method
    if isinstance(o, dict):
        return o.get(attr, default)
    else:
        return getattr(o, attr, default)


def record_usage(span: Span, usage: Dict[str, Any]) -> None:
    if not usage:
        return
    for token_type in ("input", "output"):
        num_tokens = _get_attr(usage, "%s_tokens" % token_type, None)
        if num_tokens is None:
            continue
        span.set_metric("anthropic.response.usage.%s_tokens" % token_type, num_tokens)

    if "input" in usage and "output" in usage:
        total_tokens = usage["output"] + usage["input"]
        span.set_metric("anthropic.response.usage.total_tokens", total_tokens)
