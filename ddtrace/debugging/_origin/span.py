from dataclasses import dataclass
from itertools import count
import sys
from threading import current_thread
from time import monotonic_ns
from types import FrameType
from types import FunctionType
import typing as t
import uuid

import ddtrace
from ddtrace._trace.processor import SpanProcessor
from ddtrace.debugging._probe.model import DEFAULT_CAPTURE_LIMITS
from ddtrace.debugging._probe.model import LiteralTemplateSegment
from ddtrace.debugging._probe.model import LogFunctionProbe
from ddtrace.debugging._probe.model import LogLineProbe
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._session import Session
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.debugging._uploader import SignalUploader
from ddtrace.debugging._uploader import UploaderProduct
from ddtrace.ext import EXIT_SPAN_TYPES
from ddtrace.internal.compat import Path
from ddtrace.internal.forksafe import Lock
from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import is_user_code
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.wrapping.context import WrappingContext
from ddtrace.settings.code_origin import config as co_config
from ddtrace.trace import Span


log = get_logger(__name__)


def frame_stack(frame: FrameType) -> t.Iterator[FrameType]:
    _frame: t.Optional[FrameType] = frame
    while _frame is not None:
        yield _frame
        _frame = _frame.f_back


@dataclass
class EntrySpanProbe(LogFunctionProbe):
    __span_class__ = "entry"

    @classmethod
    def build(cls, name: str, module: str, function: str) -> "EntrySpanProbe":
        message = f"{cls.__span_class__} span info for {name}, in {module}, in function {function}"

        return cls(
            probe_id=str(uuid.uuid4()),
            version=0,
            tags={},
            module=module,
            func_qname=function,
            evaluate_at=ProbeEvalTiming.ENTRY,
            template=message,
            segments=[LiteralTemplateSegment(message)],
            take_snapshot=True,
            limits=DEFAULT_CAPTURE_LIMITS,
            condition=None,
            condition_error_rate=0.0,
            rate=float("inf"),
        )


@dataclass
class ExitSpanProbe(LogLineProbe):
    __span_class__ = "exit"

    @classmethod
    def build(cls, name: str, filename: str, line: int) -> "ExitSpanProbe":
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

    @classmethod
    def from_frame(cls, frame: FrameType) -> "ExitSpanProbe":
        code = frame.f_code
        return t.cast(
            ExitSpanProbe,
            cls.build(
                name=code.co_qualname if sys.version_info >= (3, 11) else code.co_name,  # type: ignore[attr-defined]
                filename=str(Path(code.co_filename)),
                line=frame.f_lineno,
            ),
        )


@dataclass
class EntrySpanLocation:
    name: str
    line: int
    file: str
    module: str
    probe: EntrySpanProbe


class EntrySpanWrappingContext(WrappingContext):
    __enabled__ = False
    __priority__ = 199

    def __init__(self, uploader: t.Type[SignalUploader], f: FunctionType) -> None:
        super().__init__(f)

        self.uploader = uploader

        filename = str(Path(f.__code__.co_filename).resolve())
        name = f.__qualname__
        module = f.__module__
        self.location = EntrySpanLocation(
            name=name,
            line=f.__code__.co_firstlineno,
            file=filename,
            module=module,
            probe=t.cast(EntrySpanProbe, EntrySpanProbe.build(name=name, module=module, function=name)),
        )

    def __enter__(self):
        super().__enter__()

        if self.__enabled__:
            root = ddtrace.tracer.current_root_span()
            span = ddtrace.tracer.current_span()
            location = self.location
            if root is None or span is None or root.get_tag("_dd.entry_location.file") is not None:
                return self

            # Add tags to the local root
            for s in (root, span):
                s._set_tag_str("_dd.code_origin.type", "entry")

                s._set_tag_str("_dd.code_origin.frames.0.file", location.file)
                s._set_tag_str("_dd.code_origin.frames.0.line", str(location.line))
                s._set_tag_str("_dd.code_origin.frames.0.type", location.module)
                s._set_tag_str("_dd.code_origin.frames.0.method", location.name)

            self.set("start_time", monotonic_ns())

        return self

    def _close_signal(self, retval=None, exc_info=(None, None, None)):
        if not self.__enabled__:
            return

        root = ddtrace.tracer.current_root_span()
        span = ddtrace.tracer.current_span()
        if root is None or span is None:
            return

        # Check if we have any level 2 debugging sessions running for the
        # current trace
        if any(s.level >= 2 for s in Session.from_trace(root.context or span.context)):
            try:
                start_time: int = self.get("start_time")
            except KeyError:
                # Context was not opened
                return

            # Create a snapshot
            snapshot = Snapshot(
                probe=self.location.probe,
                frame=self.__frame__,
                thread=current_thread(),
                trace_context=root,
            )

            # Capture on entry
            snapshot.do_enter()

            # Correlate the snapshot with the span
            root._set_tag_str("_dd.code_origin.frames.0.snapshot_id", snapshot.uuid)
            span._set_tag_str("_dd.code_origin.frames.0.snapshot_id", snapshot.uuid)

            snapshot.do_exit(retval, exc_info, monotonic_ns() - start_time)

            if (collector := self.uploader.get_collector()) is not None:
                collector.push(snapshot)

    def __return__(self, retval):
        self._close_signal(retval=retval)
        return super().__return__(retval)

    def __exit__(self, exc_type, exc_value, traceback):
        self._close_signal(exc_info=(exc_type, exc_value, traceback))
        super().__exit__(exc_type, exc_value, traceback)


class SpanCodeOriginProcessorEntry:
    __uploader__ = SignalUploader
    __context_wrapper__ = EntrySpanWrappingContext

    _instance: t.Optional["SpanCodeOriginProcessorEntry"] = None

    _pending: t.List = []
    _lock = Lock()

    @classmethod
    def instrument_view(cls, f):
        if not _isinstance(f, FunctionType):
            return

        with cls._lock:
            if cls._instance is None:
                # Entry span code origin is not enabled, so we defer the
                # instrumentation
                cls._pending.append(f)
                return

        _f = t.cast(FunctionType, f)
        if not EntrySpanWrappingContext.is_wrapped(_f):
            log.debug("Patching entrypoint %r for code origin", f)
            EntrySpanWrappingContext(cls.__uploader__, _f).wrap()

    @classmethod
    def enable(cls):
        if cls._instance is not None:
            return

        with cls._lock:
            cls._instance = cls()

            # Instrument the pending views
            while cls._pending:
                cls.instrument_view(cls._pending.pop())

        # Register code origin for span with the snapshot uploader
        cls.__uploader__.register(UploaderProduct.CODE_ORIGIN_SPAN_ENTRY)

        # Enable the context wrapper
        cls.__context_wrapper__.__enabled__ = True

        log.debug("Code Origin for Spans (entry) enabled")

    @classmethod
    def disable(cls):
        if cls._instance is None:
            return

        # Disable the context wrapper
        cls.__context_wrapper__.__enabled__ = False

        # Unregister code origin for span with the snapshot uploader
        cls.__uploader__.unregister(UploaderProduct.CODE_ORIGIN_SPAN_ENTRY)

        cls._instance = None

        log.debug("Code Origin for Spans (entry) disabled")


class SpanCodeOriginProcessorExit(SpanProcessor):
    __uploader__ = SignalUploader

    _instance: t.Optional["SpanCodeOriginProcessorExit"] = None

    def on_span_start(self, span: Span) -> None:
        if span.span_type not in EXIT_SPAN_TYPES:
            return

        span._set_tag_str("_dd.code_origin.type", "exit")

        # Add call stack information to the exit span. Report only the part of
        # the stack that belongs to user code.
        seq = count(0)
        for frame in frame_stack(sys._getframe(1)):
            code = frame.f_code
            filename = code.co_filename

            if is_user_code(filename):
                n = next(seq)
                if n >= co_config.max_user_frames:
                    break

                span._set_tag_str(f"_dd.code_origin.frames.{n}.file", filename)
                span._set_tag_str(f"_dd.code_origin.frames.{n}.line", str(code.co_firstlineno))

                # Get the module and function name from the frame and code object. In Python3.11+ qualname
                # is available, otherwise we'll fallback to the unqualified name.
                try:
                    name = code.co_qualname  # type: ignore[attr-defined]
                except AttributeError:
                    name = code.co_name

                mod = frame.f_globals.get("__name__")
                span._set_tag_str(f"_dd.code_origin.frames.{n}.type", mod) if mod else None
                span._set_tag_str(f"_dd.code_origin.frames.{n}.method", name) if name else None

                # Check if we have any level 2 debugging sessions running for
                # the current trace
                if any(s.level >= 2 for s in Session.from_trace(span.context)):
                    # Create a snapshot
                    snapshot = Snapshot(
                        probe=ExitSpanProbe.from_frame(frame),
                        frame=frame,
                        thread=current_thread(),
                        trace_context=span,
                    )

                    # Capture on entry
                    snapshot.do_line()

                    # Collect
                    if (collector := self.__uploader__.get_collector()) is not None:
                        collector.push(snapshot)

                    # Correlate the snapshot with the span
                    span._set_tag_str(f"_dd.code_origin.frames.{n}.snapshot_id", snapshot.uuid)

    def on_span_finish(self, span: Span) -> None:
        pass

    @classmethod
    def enable(cls):
        if cls._instance is not None:
            return

        instance = cls._instance = cls()

        # Register code origin for span with the snapshot uploader
        cls.__uploader__.register(UploaderProduct.CODE_ORIGIN_SPAN_EXIT)

        # Register the processor for exit spans
        instance.register()

        log.debug("Code Origin for Spans (exit) enabled")

    @classmethod
    def disable(cls):
        if cls._instance is None:
            return

        # Unregister the processor for exit spans
        cls._instance.unregister()

        # Unregister code origin for span with the snapshot uploader
        cls.__uploader__.unregister(UploaderProduct.CODE_ORIGIN_SPAN_EXIT)

        cls._instance = None

        log.debug("Code Origin for Spans (exit) disabled")
