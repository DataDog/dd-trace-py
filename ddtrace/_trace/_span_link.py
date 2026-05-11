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

from ddtrace.internal.native._native import SpanLink


class SpanLinkKind(Enum):
    """
    A collection of standard SpanLink kinds. It's possible to use others, but these should be used when possible.
    """

    SPAN_POINTER = "span-pointer"  # Should not be used on normal SpanLinks.


__all__ = ["SpanLink", "SpanLinkKind"]
