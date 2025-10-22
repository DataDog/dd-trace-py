"""
Span Links
==========

Description
-----------

``ddtrace.trace.SpanLink`` introduces a new causal relationship between spans.
This new behavior is analogous to OpenTelemetry Span Links:
https://opentelemetry.io/docs/concepts/signals/traces/#span-links


Usage
-----

SpanLinks can be set using :meth:`ddtrace.Span.link_span(...)` Ex::

    from ddtrace.trace import tracer

    s1 = tracer.trace("s1")
    s2 = tracer.trace("s2")

    link_attributes = {"link.name": "s1_to_s2", "link.kind": "scheduled_by", "key1": "val1"}
    s1.link_span(s2.context, link_attributes)
"""

from enum import Enum
from typing import Optional

from ddtrace.internal.native._native import SpanLinkData


class SpanLinkKind(Enum):
    """
    A collection of standard SpanLink kinds. It's possible to use others, but these should be used when possible.
    """

    SPAN_POINTER = "span-pointer"  # Should not be used on normal SpanLinks.


class SpanLink(SpanLinkData):
    """
    TraceId [required]: The span's 128-bit Trace ID
    SpanId [required]: The span's 64-bit Span ID

    Flags [optional]: The span's trace-flags field, as defined in the W3C standard. If only sampling
    information is provided, the flags value must be 1 if the decision is keep, otherwise 0.

    TraceState [optional]: The span's tracestate field, as defined in the W3C standard.

    Attributes [optional]: Zero or more key-value pairs, where the key must be a non-empty string and the
    value is either a string, bool, number or an array of primitive type values.
    """

    __slots__ = []

    def __init__(
        self,
        trace_id: int,
        span_id: int,
        tracestate: Optional[str] = None,
        flags: Optional[int] = None,
        attributes: Optional[dict] = None,
        _dropped_attributes: int = 0,
    ):
        super().__init__(
            trace_id=trace_id,
            span_id=span_id,
            tracestate=tracestate,
            flags=flags,
            attributes=attributes or {},
            _dropped_attributes=_dropped_attributes,
        )

    def __repr__(self) -> str:
        return (
            f"SpanLink(trace_id={self.trace_id}, span_id={self.span_id}, attributes={self.attributes}, "
            f"tracestate={self.tracestate}, flags={self.flags}, dropped_attributes={self._dropped_attributes})"
        )
