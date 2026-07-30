"""Route tracing attribution to the stack that will consume it.

StackCollector reports span activations through link_span(). Logical-runtime providers identify the current task or
other execution context, while the ContextVar carries normalized span metadata into newly created contexts. Native
links persist across scheduler switches and are cleared by span, task, profiler, and fork lifecycle events.
"""

import contextvars
import enum
import sys
import typing
import weakref

from ddtrace.internal.datadog.profiling import stack


class SpanLinkDomain(enum.IntEnum):
    ASYNCIO_TASK = stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK
    GEVENT_GREENLET = stack.SPAN_LINK_DOMAIN_GEVENT_GREENLET


class _SpanInfo(typing.NamedTuple):
    """Tracing-neutral metadata written to native profile labels."""

    span_id: int
    local_root_span_id: int
    span_type: typing.Optional[str]


class LogicalSpanTarget(typing.NamedTuple):
    """Domain-qualified identity for a natively rendered logical stack."""

    domain: SpanLinkDomain
    identifier: int


class _SpanLinkContext(typing.NamedTuple):
    """Copyable attribution whose generation and weak source prevent stale reuse."""

    generation: int
    span_info: _SpanInfo
    span_ref: typing.Optional[typing.Callable[[], typing.Optional[typing.Any]]]


_LogicalSpanProvider = typing.Callable[[], typing.Optional[LogicalSpanTarget]]

_span_linking_enabled = False
_span_link_generation = 0
_active_span_link: contextvars.ContextVar[typing.Optional[_SpanLinkContext]] = contextvars.ContextVar(
    "ddtrace_profiling_active_span_link", default=None
)
_logical_span_providers: list[tuple[int, _LogicalSpanProvider]] = []

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


def enable_span_linking() -> None:
    global _span_linking_enabled

    _reset_span_link_state()
    _span_linking_enabled = True


def disable_span_linking() -> None:
    global _span_linking_enabled

    _span_linking_enabled = False
    _set_active_span_link(None)
    stack.reset_span_links()


def register_logical_span_provider(provider: _LogicalSpanProvider, priority: int = 0) -> None:
    """Register a non-owning resolver for a native-tracked logical execution context."""
    if any(registered is provider for _, registered in _logical_span_providers):
        return
    _logical_span_providers[:] = sorted(
        (*_logical_span_providers, (priority, provider)), key=lambda item: item[0], reverse=True
    )


def unregister_logical_span_provider(provider: _LogicalSpanProvider) -> None:
    """Stop consulting a logical execution-context resolver."""
    _logical_span_providers[:] = [
        (priority, registered) for priority, registered in _logical_span_providers if registered is not provider
    ]


def _current_logical_span_target() -> typing.Optional[LogicalSpanTarget]:
    """Resolve the highest-priority logical runtime that recognizes the current execution context."""
    for _, provider in _logical_span_providers:
        try:
            target = provider()
        except Exception:  # nosec B112
            continue
        if target is not None:
            return target
    return None


def _publish_span(target: typing.Optional[LogicalSpanTarget], span_info: _SpanInfo) -> None:
    if target is None:
        stack.link_span(span_info.span_id, span_info.local_root_span_id, span_info.span_type)
    else:
        stack.link_logical_span(
            target.domain,
            target.identifier,
            span_info.span_id,
            span_info.local_root_span_id,
            span_info.span_type,
        )


def _clear_span(target: typing.Optional[LogicalSpanTarget]) -> None:
    if target is None:
        stack.clear_span()
    else:
        stack.clear_logical_span(target.domain, target.identifier)


def link_span(span_info: typing.Optional[_SpanInfo], source: typing.Optional[typing.Any]) -> None:
    """Route a tracing activation to its physical thread or native-tracked logical context."""
    if not _span_linking_enabled:
        return
    target = _current_logical_span_target()
    if span_info is None:
        _set_active_span_link(None)
        _clear_span(target)
    else:
        span_ref = weakref.ref(source) if source is not None else None
        _set_active_span_link(_SpanLinkContext(_span_link_generation, span_info, span_ref))
        _publish_span(target, span_info)


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
    if not _span_linking_enabled:
        return False
    span_info = _inherited_span_info()
    if span_info is None:
        stack.clear_span()
        return False
    _publish_span(None, span_info)
    return True


def clear_thread_span() -> None:
    """Clear attribution for the current physical thread."""
    if _span_linking_enabled:
        stack.clear_span()


def link_logical_span(
    domain: SpanLinkDomain,
    logical_id: int,
    span_info: typing.Optional[_SpanInfo],
) -> None:
    """Seed or update attribution for a native-tracked logical execution context."""
    if not _span_linking_enabled:
        return
    target = LogicalSpanTarget(domain, logical_id)
    if span_info is None:
        _clear_span(target)
    else:
        _publish_span(target, span_info)


def link_logical_span_context(
    domain: SpanLinkDomain,
    logical_id: int,
    task_context: typing.Optional[contextvars.Context] = None,
) -> bool:
    """Seed a logical execution context from inherited profiler ContextVar state."""
    if not _span_linking_enabled:
        return False
    target = LogicalSpanTarget(domain, logical_id)
    span_info = _inherited_span_info(task_context)
    if span_info is None:
        _clear_span(target)
        return False
    _publish_span(target, span_info)
    return True


def clear_logical_span(domain: SpanLinkDomain, logical_id: int) -> None:
    """Clear attribution when a logical execution context is no longer renderable."""
    stack.clear_logical_span(domain, logical_id)


def unlink_finished_span(span_id: int) -> None:
    """Atomically remove every current target derived from a finished span."""
    if _span_linking_enabled:
        stack.unlink_finished_span(span_id)
