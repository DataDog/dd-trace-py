from enum import Enum
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace._trace._span_link import SpanLink
from ddtrace._trace._span_link import SpanLinkKind


_SPAN_POINTER_SPAN_LINK_TRACE_ID = 0
_SPAN_POINTER_SPAN_LINK_SPAN_ID = 0


class _SpanPointerDirection(Enum):
    UPSTREAM = "u"
    DOWNSTREAM = "d"


class _SpanPointer(SpanLink):
    def __init__(
        self,
        pointer_kind: str,
        pointer_direction: _SpanPointerDirection,
        pointer_hash: str,
        extra_attributes: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(
            trace_id=_SPAN_POINTER_SPAN_LINK_TRACE_ID,
            span_id=_SPAN_POINTER_SPAN_LINK_SPAN_ID,
            attributes={
                "ptr.kind": pointer_kind,
                "ptr.dir": pointer_direction.value,
                "ptr.hash": pointer_hash,
                **(extra_attributes or {}),
            },
        )

        self.kind = SpanLinkKind.SPAN_POINTER.value

    def __post_init__(self):
        # Do not want to do the trace_id and span_id checks that SpanLink does.
        pass
