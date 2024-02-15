from dataclasses import dataclass
from functools import partial as λ
from pathlib import Path
import sys
from types import FunctionType
import typing as t

import attr

import ddtrace
from ddtrace._trace.processor import SpanProcessor
from ddtrace.ext import EXIT_SPAN_TYPES
from ddtrace.internal import core
from ddtrace.internal.injection import inject_hook
from ddtrace.internal.safety import _isinstance
from ddtrace.internal.utils.inspection import linenos
from ddtrace.span import Span


@dataclass
class EntrySpanLocation:
    name: str
    start_line: int
    end_line: int
    file: str
    module: str


@attr.s
class SpanOriginProcessor(SpanProcessor):
    _instance: t.Optional["SpanOriginProcessor"] = None

    def on_span_start(self, span: Span) -> None:
        if span.span_type not in EXIT_SPAN_TYPES:
            return

        frame = sys._getframe(9)  # TODO: Pick the first user frame

        span.set_tag_str("_dd.exit_location.file", str(Path(frame.f_code.co_filename).resolve()))
        span.set_tag_str("_dd.exit_location.line", str(frame.f_lineno))

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

            def add_entry_location_info(location: EntrySpanLocation) -> None:
                root = ddtrace.tracer.current_root_span()
                if root is None or root.get_tag("_dd.entry_location.file") is not None:
                    return

                root.set_tag_str("_dd.entry_location.file", location.file)
                root.set_tag_str("_dd.entry_location.start_line", str(location.start_line))
                root.set_tag_str("_dd.entry_location.end_line", str(location.end_line))
                root.set_tag_str("_dd.entry_location.type", location.module)
                root.set_tag_str("_dd.entry_location.method", location.name)

            # Inject the span augmentation logic at the very beginning of the
            # function
            lines = linenos(f)
            start_line = min(lines)
            inject_hook(
                t.cast(FunctionType, f),
                add_entry_location_info,
                start_line,
                EntrySpanLocation(
                    name=f.__qualname__,
                    start_line=start_line,
                    end_line=max(lines),
                    file=str(Path(f.__code__.co_filename).resolve()),
                    module=f.__module__,
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
