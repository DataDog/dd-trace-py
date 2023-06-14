from collections import deque
from itertools import count
import sys
from threading import current_thread
from types import TracebackType
import typing as t
import uuid

import attr

from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.snapshot import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal.processor import SpanProcessor
from ddtrace.internal.rate_limiter import BudgetRateLimiterWithJitter as RateLimiter
from ddtrace.internal.rate_limiter import RateLimitExceeded
from ddtrace.span import Span


GLOBAL_RATE_LIMITER = RateLimiter(
    limit_rate=1,  # one trace per second
    raise_on_exceed=False,
)

# used to mark that the span have debug info captured, visible to users
DEBUG_INFO_TAG = "error.debug_info_captured"

# used to rate limit decision on the entire local trace (stored at the root span)
CAPTURE_TRACE_TAG = "_dd.debug.error.trace_captured"

# unique exception id
EXCEPTION_ID_TAG = "_dd.debug.error.exception_id"

# link to matching snapshot for every frame in the traceback
FRAME_SNAPSHOT_ID_TAG = "_dd.debug.error.%d.snapshot_id"
FRAME_FUNCTION_TAG = "_dd.debug.error.%d.function"
FRAME_FILE_TAG = "_dd.debug.error.%d.file"
FRAME_LINE_TAG = "_dd.debug.error.%d.line"


def unwind_exception_chain(
    exc,  # type: t.Optional[BaseException]
    tb,  # type: t.Optional[TracebackType]
):
    # type: (...) -> t.Tuple[t.Deque[t.Tuple[BaseException, t.Optional[TracebackType]]], t.Optional[uuid.UUID]]
    """Unwind the exception chain and assign it an ID."""
    chain = deque()  # type: t.Deque[t.Tuple[BaseException, t.Optional[TracebackType]]]

    while exc is not None:
        chain.append((exc, tb))

        try:
            if exc.__cause__ is not None:
                exc = exc.__cause__
            elif exc.__context__ is not None and not exc.__suppress_context__:
                exc = exc.__context__
            else:
                exc = None
        except AttributeError:
            # Python 2 doesn't have exception chaining
            break

        tb = getattr(exc, "__traceback__", None)

    exc_id = None
    if chain:
        # If the chain is not trivial we generate an ID for the whole chain and
        # store it on the outermost exception, if not already generated.
        exc, _ = chain[-1]
        try:
            exc_id = exc._dd_exc_id  # type: ignore[attr-defined]
        except AttributeError:
            exc._dd_exc_id = exc_id = uuid.uuid4()  # type: ignore[attr-defined]

    return chain, exc_id


@attr.s
class SpanExceptionProbe(LogLineProbe):
    @classmethod
    def build(cls, exc_id, tb):
        # type: (uuid.UUID, TracebackType) -> SpanExceptionProbe
        _exc_id = str(exc_id)
        frame = tb.tb_frame
        filename = frame.f_code.co_filename
        line = tb.tb_lineno
        name = frame.f_code.co_name
        message = "exception info for %s, in %s, line %d (exception ID %s)" % (name, filename, line, _exc_id)

        return cls(
            probe_id=_exc_id,
            version=0,
            tags={},
            source_file=filename,
            line=line,
            template=message,
            segments=[LiteralTemplateSegment(message)],
            take_snapshot=True,
            limits=DEFAULT_CAPTURE_LIMITS,
            condition=None,
            condition_error_rate=0.0,
            rate=float("inf"),
        )


@attr.s
class SpanExceptionSnapshot(Snapshot):
    exc_id = attr.ib(type=t.Optional[uuid.UUID], default=None)

    @property
    def data(self):
        # type: () -> t.Dict[str, t.Any]
        data = super(SpanExceptionSnapshot, self).data

        data.update({"exception-id": str(self.exc_id)})

        return data


def can_capture(span):
    # type: (Span) -> bool
    # We determine if we should capture the exception information from the span
    # by looking at its local root. If we have budget to capture, we mark the
    # root as "info captured" and return True. If we don't have budget, we mark
    # the root as "info not captured" and return False. If the root is already
    # marked, we return the mark.
    root = span._local_root
    if root is None:
        return False

    info_captured = root.get_tag(CAPTURE_TRACE_TAG)

    if info_captured == "false":
        return False

    if info_captured == "true":
        return True

    if info_captured is None:
        result = GLOBAL_RATE_LIMITER.limit() is not RateLimitExceeded
        root.set_tag_str(CAPTURE_TRACE_TAG, str(result).lower())
        return result

    raise ValueError("unexpected value for %s: %r" % (CAPTURE_TRACE_TAG, info_captured))


@attr.s
class SpanExceptionProcessor(SpanProcessor):
    collector = attr.ib(type=SignalCollector)

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        if not (span.error and can_capture(span)):
            # No error or budget to capture
            return

        _, exc, _tb = sys.exc_info()

        chain, exc_id = unwind_exception_chain(exc, _tb)
        if not chain or exc_id is None:
            # No exceptions to capture
            return

        seq = count(1)  # 1-based sequence number

        while chain:
            exc, _tb = chain.pop()  # LIFO: reverse the chain

            if _tb is None or _tb.tb_frame is None:
                # If we don't have a traceback there isn't much we can do
                continue

            # DEV: We go from the handler up to the root exception
            while _tb and _tb.tb_frame:
                frame = _tb.tb_frame
                code = frame.f_code
                seq_nr = next(seq)

                # TODO: Check if it is user code; if not, skip. We still
                #       generate a sequence number.

                try:
                    snapshot_id = frame.f_locals["_dd_debug_snapshot_id"]
                except KeyError:
                    # We don't have a snapshot for the frame so we create one
                    snapshot = SpanExceptionSnapshot(
                        probe=SpanExceptionProbe.build(exc_id, _tb),
                        frame=frame,
                        thread=current_thread(),
                        trace_context=span,
                        exc_id=exc_id,
                    )

                    # Capture
                    snapshot.line()

                    # Collect
                    self.collector.push(snapshot)

                    # Memoize
                    frame.f_locals["_dd_debug_snapshot_id"] = snapshot_id = snapshot.uuid

                # Add correlation tags on the span
                span.set_tag_str(FRAME_SNAPSHOT_ID_TAG % seq_nr, snapshot_id)
                span.set_tag_str(FRAME_FUNCTION_TAG % seq_nr, code.co_name)
                span.set_tag_str(FRAME_FILE_TAG % seq_nr, code.co_filename)
                span.set_tag_str(FRAME_LINE_TAG % seq_nr, str(_tb.tb_lineno))

                # Move up the stack
                _tb = _tb.tb_next

            span.set_tag_str(DEBUG_INFO_TAG, "true")
            span.set_tag_str(EXCEPTION_ID_TAG, str(exc_id))
