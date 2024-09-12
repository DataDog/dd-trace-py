# A private API for now.

from enum import Enum
from typing import Any
from typing import Dict
from typing import NamedTuple
from typing import Optional

from ddtrace._trace._span_link import SpanLink
from ddtrace._trace._span_link import SpanLinkKind
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


class _SpanPointerDirection(Enum):
    UPSTREAM = "u"
    DOWNSTREAM = "d"


class _SpanPointerDescription(NamedTuple):
    # Not to be confused with _SpanPointer. This class describes the parameters
    # required to attach a span pointer to a Span. It lets us decouple code
    # that calculates span pointers from code that actually attaches them to
    # the right Span.

    pointer_kind: str
    pointer_direction: _SpanPointerDirection
    pointer_hash: str
    extra_attributes: Dict[str, Any]


_SPAN_POINTER_SPAN_LINK_TRACE_ID = 0
_SPAN_POINTER_SPAN_LINK_SPAN_ID = 0


class _SpanPointer(SpanLink):
    # The class to privately store span pointers and serialize them alongside
    # Span Links. The span pointer methods on Span objects should be used
    # instead of instantiating this class directly.

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
