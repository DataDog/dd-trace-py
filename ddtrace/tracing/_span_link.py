from typing import Optional

import attr


_PRIMITIVE_TYPES = (int, float, bool)


@attr.s
class SpanLink:
    """
    TraceId [required]: The span's 128-bit Trace ID
    SpanId [required]: The span's 64-bit Span ID
    TraceFlags [optional]: The span's trace-flags field, as defined in the W3C standard

    This field must be omitted if no sampling information was provided. If the original
    OpenTelemetry flags are known, they should be directly provided here. If only sampling
    information is provided, the flags value must be 1 if the decision is keep, otherwise 0.

    TraceState [optional]: The span's tracestate field, as defined in the W3C standard
    If datadog (or b3) headers are given, they must be transformed to an equivalent tracestate
    representation (see [RFC] Implementing W3C Trace Context Propagation).

    Attributes: Zero or more key-value pairs, which have the following properties
    The key must be a non-empty string
    The value is either:
    A primitive type: string, bool, or number
    An array of primitive type values
    Optional: An array of arrays.
    """

    trace_id = attr.ib(type=int)
    span_id = attr.ib(type=int)
    tracestate = attr.ib(type=Optional[str], default=None)
    traceflags = attr.ib(type=Optional[str], default=None)
    attributes = attr.ib(type=dict, default=dict())
    name = attr.ib(type=Optional[str], default=None)
    kind = attr.ib(type=Optional[str], default=None)
    _dropped_attributes = attr.ib(type=int, default=0)

    def to_dict(self):
        d = {"trace_id": self.trace_id, "span_id": self.span_id, "attributes": self.attributes}
        if self.tracestate:
            d["tracestate"] = self.tracestate
        if self.traceflags:
            d["traceflags"] = self.traceflags
        if self.name:
            d["attributes"]["link.name"] = self.name
        if self.kind:
            d["attributes"]["link.kind"] = self.kind

        return d
