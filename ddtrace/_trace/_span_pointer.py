from enum import Enum
from hashlib import sha256
import random
from typing import Any
from typing import Dict
from typing import NamedTuple
from typing import Optional

from ddtrace._trace._span_link import SpanLink
from ddtrace._trace._span_link import SpanLinkKind
from ddtrace._trace.telemetry import record_span_pointer_calculation_issue
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


_SPAN_POINTER_SPAN_LINK_TRACE_ID = 0
_SPAN_POINTER_SPAN_LINK_SPAN_ID = 0


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


_STANDARD_HASHING_FUNCTION_FAILURE_PREFIX = "HashingFailure"


def _standard_hashing_function(*elements: bytes) -> str:
    try:
        if not elements:
            return _standard_hashing_function_failure("elements must not be empty")

        # Please see the tests for more details about this logic.
        return sha256(b"|".join(elements)).hexdigest()[:32]

    except Exception as e:
        return _standard_hashing_function_failure(str(e))


def _standard_hashing_function_failure(reason: str) -> str:
    log.debug(
        "span pointers: failed to generate standard hash for span pointer: %s",
        reason,
    )
    record_span_pointer_calculation_issue(
        context="standard_hashing_function",
    )

    return _add_random_suffix(
        prefix=_STANDARD_HASHING_FUNCTION_FAILURE_PREFIX,
        minimum_length=32,
    )


def _add_random_suffix(*, prefix: str, minimum_length: int) -> str:
    if len(prefix) >= minimum_length:
        return prefix

    suffix = "".join(random.choice("0123456789abcdef") for _ in range(minimum_length - len(prefix)))  # nosec

    return prefix + suffix
