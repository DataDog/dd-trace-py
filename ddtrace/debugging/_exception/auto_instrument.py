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
        filename = frame.f_code.co_filename
        line = frame.f_lineno
        fn = frame.f_code.co_name
        message = "exception info for %s, in %s, line %d (exception ID %s)" % (fn, filename, line, exc_id)

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

        _, exc, _tb = sys.exc_info()
        if _tb is None or _tb.tb_frame is None:
            # If we don't have a traceback there isn't much we can do
            return

        seq = count(1)
        try:
            exc_id = exc._dd_exc_id  # type: ignore[union-attr]
        except AttributeError:
            # We haven't seen this exception before so we generate an ID for it
            exc._dd_exc_id = exc_id = uuid.uuid4()  # type: ignore[union-attr]

        # DEV: We go from the handler up to the root exception
        while _tb:
            frame = _tb.tb_frame
            code = frame.f_code
            seq_nr = next(seq)

            try:
                snapshot_id = frame.f_locals["_dd_debug_snapshot_id"]
            except KeyError:
                # We don't have a snapshot for the frame so we create one
                snapshot = SpanExceptionSnapshot(
                    probe=SpanExceptionProbe.build(exc_id, frame),
                    frame=frame,
                    thread=current_thread(),
                    trace_context=span,
                    exc_id=exc_id,
                )

                # Capture
                snapshot.line()

                self.collector.push(snapshot)

                frame.f_locals["_dd_debug_snapshot_id"] = snapshot_id = snapshot.uuid

            # Add correlation tags on the span
            span.set_tag_str("_dd.debug.error.%d.snapshot.id" % seq_nr, snapshot_id)
            span.set_tag_str("_dd.debug.error.%d.function" % seq_nr, code.co_name)
            span.set_tag_str("_dd.debug.error.%d.file" % seq_nr, code.co_filename)
            span.set_tag_str("_dd.debug.error.%d.line" % seq_nr, str(frame.f_lineno))

            _tb = _tb.tb_next

        span.set_tag_str("error.debug.info-captured", "true")
        span.set_tag_str("_dd.debug.error.exception-id", str(exc_id))
