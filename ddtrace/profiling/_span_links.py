"""Coordinate profiler span attribution across tracing and profiler lifecycles.

StackCollector reports span activations through link_span(). The ContextVar carries normalized span metadata into
copied contexts so execution integrations can publish inherited attribution when they start running. Native links and
inherited metadata are invalidated by span, profiler, and fork lifecycle events.
"""

import contextvars
import sys
import typing
import weakref

from ddtrace.internal.datadog.profiling import stack


class _SpanInfo(typing.NamedTuple):
    """Tracing-neutral metadata written to native profile labels."""

    span_id: int
    local_root_span_id: int
    span_type: typing.Optional[str]


class _SpanLinkContext(typing.NamedTuple):
    """Copyable attribution whose generation and weak source prevent stale reuse."""

    generation: int
    span_info: _SpanInfo
    span_ref: typing.Optional[typing.Callable[[], typing.Optional[typing.Any]]]


_CurrentSpanProvider = typing.Callable[[], tuple[typing.Optional[_SpanInfo], typing.Optional[typing.Any]]]

_span_linking_active = False
_span_link_generation = 0
_active_span_link: contextvars.ContextVar[typing.Optional[_SpanLinkContext]] = contextvars.ContextVar(
    "ddtrace_profiling_active_span_link", default=None
)
_current_span_provider: typing.Optional[_CurrentSpanProvider] = None

if sys.version_info < (3, 12):
    from ddtrace.internal.native._native import safe_contextvar_set

    def _set_active_span_link(value: typing.Optional[_SpanLinkContext]) -> None:
        safe_contextvar_set(_active_span_link, value)

else:
    _set_active_span_link = _active_span_link.set  # type: ignore[assignment]


def _reset_span_link_state() -> None:
    """Invalidate native links and ContextVar copies inherited from an earlier lifecycle."""
    global _span_link_generation

    stack.reset_span_links()
    _span_link_generation += 1
    _set_active_span_link(None)


def start_span_linking(current_span_provider: typing.Optional[_CurrentSpanProvider] = None) -> None:
    global _current_span_provider, _span_linking_active

    _reset_span_link_state()
    _current_span_provider = current_span_provider
    _span_linking_active = True
    link_current_span()


def stop_span_linking() -> None:
    global _current_span_provider, _span_linking_active

    _span_linking_active = False
    _current_span_provider = None
    _set_active_span_link(None)
    stack.reset_span_links()


def _publish_span(span_info: _SpanInfo) -> None:
    stack.link_span(span_info.span_id, span_info.local_root_span_id, span_info.span_type)


def link_span(span_info: typing.Optional[_SpanInfo], source: typing.Optional[typing.Any]) -> None:
    """Publish a tracing activation to the current physical thread."""
    if not _span_linking_active:
        return
    if span_info is None:
        _set_active_span_link(None)
        stack.clear_span()
    else:
        span_ref = weakref.ref(source) if source is not None else None
        _set_active_span_link(_SpanLinkContext(_span_link_generation, span_info, span_ref))
        _publish_span(span_info)


def link_current_span() -> bool:
    """Republish the configured tracer's current context after a profiler lifecycle transition."""
    if not _span_linking_active or _current_span_provider is None:
        return False
    try:
        span_info, source = _current_span_provider()
    except Exception:
        return False
    link_span(span_info, source)
    return span_info is not None


def _inherited_span_info(task_context: typing.Optional[contextvars.Context] = None) -> typing.Optional[_SpanInfo]:
    linked_span = task_context.get(_active_span_link) if task_context is not None else _active_span_link.get()
    if linked_span is None or linked_span.generation != _span_link_generation:
        return None
    source_span = linked_span.span_ref() if linked_span.span_ref is not None else None
    if linked_span.span_ref is not None and (source_span is None or source_span.finished):
        return None
    return linked_span.span_info


def link_thread_span_context() -> bool:
    """Link the current physical thread from inherited profiler ContextVar state."""
    if not _span_linking_active:
        return False
    span_info = _inherited_span_info()
    if span_info is None:
        stack.clear_span()
        return False
    _publish_span(span_info)
    return True


def clear_thread_span() -> None:
    """Clear attribution for the current physical thread."""
    if _span_linking_active:
        stack.clear_span()


def unlink_finished_span(span_id: int) -> None:
    """Remove every current attribution derived from a finished span."""
    if _span_linking_active:
        stack.unlink_finished_span(span_id)
