from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from time import monotonic_ns
from types import FunctionType
from types import MethodType
import typing as t
import weakref

import ddtrace
from ddtrace.debugging._session import Session
from ddtrace.internal.compat import NO_EXCEPTION
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.threads import RLock
from ddtrace.internal.wrapping.context import LazyWrappingContext


if t.TYPE_CHECKING:
    from ddtrace.debugging._origin._span_snapshot import EntrySpanProbe
    from ddtrace.debugging._uploader import SignalUploader


log = get_logger(__name__)
_probe_lock = RLock()


@dataclass
class EntrySpanLocation:
    name: str
    line: int
    file: str
    module: str
    _probe: t.Optional["EntrySpanProbe"] = field(default=None, repr=False)

    @property
    def probe(self) -> "EntrySpanProbe":
        if self._probe is None:
            from ddtrace.debugging._origin._span_snapshot import EntrySpanProbe

            with _probe_lock:
                if self._probe is None:
                    self._probe = EntrySpanProbe.build(name=self.name, module=self.module, function=self.name)
        return self._probe


class EntrySpanWrappingContext(LazyWrappingContext):
    """Entry span wrapping context.

    This context is lazy to avoid paid any upfront instrumentation costs for
    large functions that might not get invoked. Instead, the actual wrapping
    will be performed on the first invocation.
    """

    __enabled__ = False
    __priority__ = 199

    def __init__(self, uploader: t.Callable[[], t.Optional[type["SignalUploader"]]], f: FunctionType) -> None:
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
        )

    def __enter__(self) -> "EntrySpanWrappingContext":
        super().__enter__()

        if self.__enabled__:
            root = ddtrace.tracer.current_root_span()
            span = ddtrace.tracer.current_span()
            location = self.location
            if root is None or span is None or root.get_tag("_dd.code_origin.type") == "entry":
                # If the root span already has entry location tags, it means
                # another entry span wrapping context has already been entered
                # upstream, so we skip adding code origin tags to avoid
                # overwriting the original entry point information
                return self

            # Add tags to the local root
            for s in (root, span):
                s._set_attribute("_dd.code_origin.type", "entry")

                s._set_attribute("_dd.code_origin.frames.0.file", location.file)
                s._set_attribute("_dd.code_origin.frames.0.line", str(location.line))
                s._set_attribute("_dd.code_origin.frames.0.type", location.module)
                s._set_attribute("_dd.code_origin.frames.0.method", location.name)

            self.set("start_time", monotonic_ns())

        return self

    def _close_signal(self, retval: t.Any = None, exc_info: ExcInfoType = NO_EXCEPTION) -> None:
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

            from ddtrace.debugging._origin._span_snapshot import capture_snapshot

            if (uploader := self.uploader()) is not None:
                capture_snapshot(
                    self.location.probe, self.__frame__, root, span, start_time, retval, exc_info, uploader
                )

    def __return__(self, retval: t.Any) -> t.Any:
        self._close_signal(retval=retval)
        return super().__return__(retval)

    def __exit__(
        self,
        exc_type: t.Optional[type[BaseException]],
        exc_value: t.Optional[BaseException],
        traceback: t.Optional[t.Any],
    ) -> None:
        self._close_signal(exc_info=t.cast(ExcInfoType, (exc_type, exc_value, traceback)))
        super().__exit__(exc_type, exc_value, traceback)


class SpanCodeOriginProcessorEntry:
    __uploader__: t.Optional[type["SignalUploader"]] = None
    __context_wrapper__ = EntrySpanWrappingContext

    _instance: t.Optional["SpanCodeOriginProcessorEntry"] = None

    # Weak references so that functions defined dynamically (e.g. inner
    # functions created afresh on every call) don't get leaked while this
    # product is disabled and views are only pending instrumentation.
    _pending: "weakref.WeakSet[FunctionType]" = weakref.WeakSet()
    _lock = RLock()

    @classmethod
    def instrument_view(cls, f: t.Union[FunctionType, MethodType]) -> None:
        if isinstance(f, MethodType):
            f = t.cast(FunctionType, f.__func__)
        if not _isinstance(f, FunctionType):
            log.debug("Cannot instrument view %r: not a function", f)
            return

        with cls._lock:
            if cls._instance is None:
                # Entry span code origin is not enabled, so we defer the
                # instrumentation
                cls._pending.add(f)
                return

        _f = t.cast(FunctionType, f)
        if not EntrySpanWrappingContext.is_wrapped(_f):
            log.debug("Lazy wrapping entrypoint %r for code origin", f)
            EntrySpanWrappingContext(cls._snapshot_uploader, _f).wrap()

    @classmethod
    def _snapshot_uploader(cls) -> t.Optional[type["SignalUploader"]]:
        from ddtrace.debugging._uploader import SignalUploader
        from ddtrace.debugging._uploader import UploaderProduct

        with cls._lock:
            if cls._instance is None:
                return None
            if cls.__uploader__ is None:
                cls.__uploader__ = SignalUploader
            cls.__uploader__.register(UploaderProduct.CODE_ORIGIN_SPAN_ENTRY)
            return cls.__uploader__

    @classmethod
    def enable(cls) -> None:
        if cls._instance is not None:
            return

        with cls._lock:
            cls._instance = cls()

            # Instrument the pending views
            pending, cls._pending = list(cls._pending), weakref.WeakSet()
            for f in pending:
                cls.instrument_view(f)

        if cls.__uploader__ is not None:
            cls._snapshot_uploader()

        # Enable the context wrapper
        cls.__context_wrapper__.__enabled__ = True

        log.debug("Code Origin for Spans (entry) enabled")

    @classmethod
    def disable(cls) -> None:
        if cls._instance is None:
            return

        # Disable the context wrapper
        cls.__context_wrapper__.__enabled__ = False

        if cls.__uploader__ is not None:
            from ddtrace.debugging._uploader import UploaderProduct

            cls.__uploader__.unregister(UploaderProduct.CODE_ORIGIN_SPAN_ENTRY)

        cls._instance = None

        log.debug("Code Origin for Spans (entry) disabled")
