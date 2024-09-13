# A private API for now.

from enum import Enum
from hashlib import sha256
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


class _StandardHashingRules:
    # This is a bit of a strange class. Its instances behave like simple
    # function. This allows us to do the hex_digits calculation once. It also
    # allows us to write tests for the chosen base_hashing_function instead of
    # having to add runtime sanity checks. This class should be instantiated
    # only once, internally, and the result stored as a the
    # _standard_hashing_function "function" below.

    separator = b"|"
    bits_per_hex_digit = 4
    desired_bits = 128
    hex_digits = desired_bits // bits_per_hex_digit

    # We need to call it a staticmethod to keep python from adding a `self`
    # argument to the function call.
    base_hashing_function = staticmethod(sha256)

    def __call__(self, *elements: bytes) -> str:
        if not elements:
            raise ValueError("elements must not be empty")

        return self.base_hashing_function(self.separator.join(elements)).hexdigest()[: self.hex_digits]


_standard_hashing_function = _StandardHashingRules()
