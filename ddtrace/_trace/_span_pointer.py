"""
Span Pointers
=============

Description
-----------

``ddtrace.trace.SpanPointer`` is used in situations where a
:class:`ddtrace.trace.SpanLink` cannot be used. This comes up when we want to
link spans but cannot pass the span context between them. The Span Pointer
relies on consistent hashing rules to identify the linking entity between the
spans. When both the upstream and downstream spans point to the same entity
(and are both sampled), the Spans are linked in the Datadog UI.

Usage
-----

Span Links can be created using :meth:`ddtrace.Span.add_span_pointer(...)` Ex::

    from ddtrace import tracer

    s1 = tracer.trace("s1")
    s1.add_span_pointer(
        kind="some.thing",
        direction=SpanPointerDirection.DOWNSTREAM,
        hash="same-hash",
    )

    // somewhere else where we cannot call `s1.link_span(s2.context)`

    s2 = tracer.trace("s2")
    s2.add_span_pointer(
        kind="some.thing",
        direction=SpanPointerDirection.UPSTREAM,
        hash="same-hash",
    )
"""

import dataclasses
from enum import Enum
from typing import NewType
from ddtrace._trace._span_link import _span_link_shaped_dict


SpanPointerHash = NewType("SpanPointerHash", str)


class SpanPointerDirection(Enum):
    """
    SpanPointerDirection represents the direction of the Pointer from the
    perspective of the Span. The Span that creates an Entity would have a
    pointer with direction DOWNSTREAM. The Span that is triggered by the Entity
    would have a pointer with direction UPSTREAM.
    """

    UPSTREAM = "u"
    DOWNSTREAM = "d"


SPAN_POINTER_SPAN_LINK_TRACE_ID = 0
SPAN_POINTER_SPAN_LINK_SPAN_ID = 0


@dataclasses.dataclass
class SpanPointer:
    kind: str
    direction: SpanPointerDirection
    hash: SpanPointerHash
    extra_attributes: dict = dataclasses.field(default_factory=dict)

    def to_dict(self):
        return _span_link_shaped_dict(
            trace_id=SPAN_POINTER_SPAN_LINK_TRACE_ID,
            span_id=SPAN_POINTER_SPAN_LINK_SPAN_ID,
            attributes={
                "link.kind": "span-pointer",
                "ptr.kind": self.kind,
                "ptr.dir": self.direction.value,
                "ptr.hash": self.hash,
                **self.extra_attributes,
            },
            dropped_attributes_count=0,
            tracestate=None,
            flags=None,
        )
