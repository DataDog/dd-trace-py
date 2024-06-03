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

    input_tokens = _get_attr(usage, "input_tokens", None)
    output_tokens = _get_attr(usage, "output_tokens", None)

    span.set_metric("anthropic.response.usage.input_tokens", input_tokens)
    span.set_metric("anthropic.response.usage.output_tokens", output_tokens)

    if input_tokens is not None and output_tokens is not None:
        span.set_metric("anthropic.response.usage.total_tokens", input_tokens + output_tokens)
