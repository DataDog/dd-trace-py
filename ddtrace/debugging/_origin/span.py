from dataclasses import dataclass
from functools import partial as λ
from pathlib import Path
import sys
from threading import current_thread
from types import FrameType
from types import FunctionType
import typing as t
import uuid

import attr

import ddtrace
from ddtrace._trace.processor import SpanProcessor
from ddtrace.debugging._debugger import Debugger
from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.ext import EXIT_SPAN_TYPES
from ddtrace.internal import core
from ddtrace.internal.injection import inject_hook
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.inspection import linenos
from ddtrace.span import Span


@attr.s
class SpanOriginProbe(LogLineProbe):
    __span_class__: t.Optional[str] = None

    @classmethod
    def build(cls, name: str, filename: str, line: int) -> "SpanOriginProbe":
        message = f"{cls.__span_class__} span info for {name}, in {filename}, at {line}"

        return cls(
            probe_id=str(uuid.uuid4()),
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
class EntrySpanProbe(SpanOriginProbe):
    __span_class__ = "entry"


@attr.s
class ExitSpanProbe(SpanOriginProbe):
    __span_class__ = "exit"

    @classmethod
    def from_frame(cls, frame: FrameType) -> "ExitSpanProbe":
        code = frame.f_code
        return t.cast(
            ExitSpanProbe,
            cls.build(
                name=code.co_qualname if sys.version_info >= (3, 11) else code.co_name,  # type: ignore[attr-defined]
                filename=str(Path(code.co_filename).resolve()),
                line=frame.f_lineno,
            ),
        )


@dataclass
class EntrySpanLocation:
    name: str
    start_line: int
    end_line: int
    file: str
    module: str
    probe: EntrySpanProbe


def add_entry_location_info(location: EntrySpanLocation) -> None:
    root = ddtrace.tracer.current_root_span()
    if root is None or root.get_tag("_dd.entry_location.file") is not None:
        return

    # Add tags to the local root
    root.set_tag_str("_dd.entry_location.file", location.file)
    root.set_tag_str("_dd.entry_location.start_line", str(location.start_line))
    root.set_tag_str("_dd.entry_location.end_line", str(location.end_line))
    root.set_tag_str("_dd.entry_location.type", location.module)
    root.set_tag_str("_dd.entry_location.method", location.name)

    # Create a snapshot
    snapshot = Snapshot(
        probe=location.probe,
        frame=sys._getframe(1),
        thread=current_thread(),
        trace_context=root,
    )

    # Capture on entry
    snapshot.line()

    # Collect
    Debugger.get_collector().push(snapshot)

    # Correlate the snapshot with the span
    root.set_tag_str("_dd.entry_location.snapshot_id", snapshot.uuid)


@attr.s
class SpanOriginProcessor(SpanProcessor):
    _instance: t.Optional["SpanOriginProcessor"] = None

    def on_span_start(self, span: Span) -> None:
        if span.span_type not in EXIT_SPAN_TYPES:
            return

        # Get the user frame
        frame = sys._getframe(9)  # TODO: Pick the first user frame

        # Add location information to the exit span
        span.set_tag_str("_dd.exit_location.file", str(Path(frame.f_code.co_filename).resolve()))
        span.set_tag_str("_dd.exit_location.line", str(frame.f_lineno))

        # Create a snapshot
        snapshot = Snapshot(
            probe=ExitSpanProbe.from_frame(frame),
            frame=frame,
            thread=current_thread(),
            trace_context=span,
        )

        # Capture on entry
        snapshot.line()

        # Collect
        Debugger.get_collector().push(snapshot)

        # Correlate the snapshot with the span
        span.set_tag_str("_dd.exit_location.snapshot_id", snapshot.uuid)

    def on_span_finish(self, span: Span) -> None:
        pass

    @classmethod
    def enable(cls):
        if cls._instance is not None:
            return

        @λ(core.on, "service_entrypoint.patch")
        def _(f: t.Callable) -> None:
            if not _isinstance(f, FunctionType):
                return

            if hasattr(f, "__dd_wrapped__"):
                f = f.__dd_wrapped__

            # Inject the span augmentation logic at the very beginning of the
            # function
            lines = linenos(f)
            start_line = min(lines)
            filename = str(Path(f.__code__.co_filename).resolve())
            name = f.__qualname__
            inject_hook(
                t.cast(FunctionType, f),
                add_entry_location_info,
                start_line,
                EntrySpanLocation(
                    name=name,
                    start_line=start_line,
                    end_line=max(lines),
                    file=filename,
                    module=f.__module__,
                    probe=t.cast(EntrySpanProbe, EntrySpanProbe.build(name=name, filename=filename, line=start_line)),
                ),
            )

        instance = cls._instance = cls()
        instance.register()

    @classmethod
    def disable(cls):
        if cls._instance is None:
            return

        cls._instance.unregister()
        cls._instance = None

        # TODO: The core event hook is still registered. Currently there is no
        # way to unregister it.
