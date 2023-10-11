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

    from ddtrace import tracer

    s1 = tracer.trace("s1")
    s2 = tracer.trace("s2")

    link_attributes = {"link.name": "s1_to_s2", "link.kind": "scheduled_by", "key1": "val1"}
    s1.link_span(s2.context, link_attributes)
"""

from typing import Optional

import attr


@attr.s
class SpanLink:
    """
    TraceId [required]: The span's 128-bit Trace ID
    SpanId [required]: The span's 64-bit Span ID

    Flags [optional]: The span's trace-flags field, as defined in the W3C standard. If only sampling
    information is provided, the flags value must be 1 if the decision is keep, otherwise 0.

    TraceState [optional]: The span's tracestate field, as defined in the W3C standard.

    Attributes [optional]: Zero or more key-value pairs, where the key must be a non-empty string and the
    value is either a string, bool, number or an array of primitive type values.
    """

    trace_id = attr.ib(type=int)
    span_id = attr.ib(type=int)
    tracestate = attr.ib(type=Optional[str], default=None)
    flags = attr.ib(type=Optional[int], default=None)
    attributes = attr.ib(type=dict, default=dict())
    _dropped_attributes = attr.ib(type=int, default=0)

    @property
    def name(self):
        return self.attributes["link.name"]

    @name.setter
    def name(self, value):
        self.attributes["link.name"] = value

    @property
    def kind(self):
        return self.attributes["link.kind"]

    @kind.setter
    def kind(self, value):
        self.attributes["link.kind"] = value

    def _drop_attribute(self, key):
        if key not in self.attributes:
            raise ValueError(f"Invalid key: {key}")
        del self.attributes[key]
        self._dropped_attributes += 1

    def to_dict(self):
        d = {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
        }
        if self.attributes:
            d["attributes"] = {k: str(v) for k, v in self.attributes.items()}
        if self._dropped_attributes > 0:
            d["dropped_attributes_count"] = self._dropped_attributes
        if self.tracestate:
            d["tracestate"] = self.tracestate
        if self.flags is not None:
            d["flags"] = self.flags

        return d
