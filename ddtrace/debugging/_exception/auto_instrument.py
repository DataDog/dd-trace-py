from itertools import count
import sys
from threading import current_thread
from types import FrameType
import typing as t
import uuid

import attr

from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._signal.collector import SignalCollector
from ddtrace.debugging._signal.snapshot import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal.processor import SpanProcessor
from ddtrace.span import Span


@attr.s
class SpanExceptionProbe(LogLineProbe):
    @classmethod
    def build(cls, exc_id, frame):
        # type: (uuid.UUID, FrameType) -> SpanExceptionProbe
        _exc_id = str(exc_id)
        message = "Exception Debugging (exception %s)" % exc_id

        return cls(
            probe_id=_exc_id,
            version=0,
            tags={},
            source_file=frame.f_code.co_filename,
            line=frame.f_lineno,
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
    seq_nr = attr.ib(type=t.Optional[int], default=None)

    @property
    def data(self):
        # type: () -> t.Dict[str, t.Any]
        data = super(SpanExceptionSnapshot, self).data

        data.update(
            exc_id=str(self.exc_id),
            seq_nr=self.seq_nr,
        )

        return data


@attr.s
class SpanExceptionProcessor(SpanProcessor):
    collector = attr.ib(type=SignalCollector)

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        if not span.error:
            # No error to capture
            return

        _type, _exc, _tb = sys.exc_info()
        if _tb is None or _tb.tb_frame is None:
            # If we don't have a traceback there isn't much we can do
            return

        seq_nr = count()
        exc_id = uuid.uuid4()

        # DEV: We go from the handler up to the root exception
        while _tb:
            frame = _tb.tb_frame
            n = next(seq_nr)

            snapshot = SpanExceptionSnapshot(
                probe=SpanExceptionProbe.build(exc_id, frame),
                frame=frame,
                thread=current_thread(),
                trace_context=span,
                exc_id=exc_id,
                seq_nr=n,
            )

            # Capture
            snapshot.line()

            self.collector.push(snapshot)

            _tb = _tb.tb_next
