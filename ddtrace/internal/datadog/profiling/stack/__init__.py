# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    import contextvars
    import enum
    import sys
    import typing
    import weakref

    from ddtrace._trace import context
    from ddtrace._trace import span as ddspan
    from ddtrace.internal.datadog.profiling import context_meta

    from . import _stack
    from ._stack import *  # noqa: F403, F401  # type: ignore[assignment]

    class SpanLinkDomain(enum.IntEnum):
        ASYNCIO_TASK = _stack.SPAN_LINK_DOMAIN_ASYNCIO_TASK
        GEVENT_GREENLET = _stack.SPAN_LINK_DOMAIN_GEVENT_GREENLET

    class _SpanInfo(typing.NamedTuple):
        span_id: int
        local_root_span_id: int
        span_type: typing.Optional[str]

    class LogicalSpanTarget(typing.NamedTuple):
        domain: SpanLinkDomain
        identifier: int

    class _SpanLinkContext(typing.NamedTuple):
        generation: int
        span_info: _SpanInfo
        span_ref: typing.Optional[typing.Callable[[], typing.Optional[ddspan.Span]]]

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
        global _span_link_generation

        _stack.reset_span_links()
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
        _stack.reset_span_links()

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
        for _, provider in _logical_span_providers:
            try:
                target = provider()
            except Exception:  # nosec B112
                continue
            if target is not None:
                return target
        return None

    def _span_info(span: typing.Optional[typing.Union[context.Context, ddspan.Span]]) -> typing.Optional[_SpanInfo]:
        if isinstance(span, ddspan.Span):
            span_id = span.span_id
            # A Span whose _parent is None but parent_id is set was created with child_of=Context. Its local root is
            # the new span, so read distributed local-root metadata from the parent Context.
            if span._parent is None and span.parent_id is not None and span._parent_context is not None:
                propagated_root_span_id, propagated_root_span_type = context_meta.read_profiler_link(
                    span._parent_context
                )
                local_root_span_id = propagated_root_span_id or span._local_root.span_id
                local_root_span_type = propagated_root_span_type or span._local_root.span_type
            else:
                local_root_span_id = span._local_root.span_id
                local_root_span_type = span._local_root.span_type
            return _SpanInfo(span_id, local_root_span_id, local_root_span_type)
        if isinstance(span, context.Context) and span.span_id is not None:
            local_root_span_id, span_type = context_meta.read_profiler_link(span)
            return _SpanInfo(span.span_id, local_root_span_id, span_type)
        return None

    def _publish_span(target: typing.Optional[LogicalSpanTarget], span_info: _SpanInfo) -> None:
        if target is None:
            _stack.link_span(span_info.span_id, span_info.local_root_span_id, span_info.span_type)
        else:
            _stack.link_logical_span(
                target.domain,
                target.identifier,
                span_info.span_id,
                span_info.local_root_span_id,
                span_info.span_type,
            )

    def _clear_span(target: typing.Optional[LogicalSpanTarget]) -> None:
        if target is None:
            _stack.clear_span()
        else:
            _stack.clear_logical_span(target.domain, target.identifier)

    def link_span(span: typing.Optional[typing.Union[context.Context, ddspan.Span]]) -> None:
        """Route a tracing activation to its physical thread or native-tracked logical context."""
        if not _span_linking_enabled:
            return
        target = _current_logical_span_target()
        span_info = _span_info(span)
        if span_info is None:
            _set_active_span_link(None)
            _clear_span(target)
        else:
            span_ref = weakref.ref(span) if isinstance(span, ddspan.Span) else None
            _set_active_span_link(_SpanLinkContext(_span_link_generation, span_info, span_ref))
            _publish_span(target, span_info)

    def link_logical_span(
        domain: SpanLinkDomain,
        logical_id: int,
        span: typing.Optional[typing.Union[context.Context, ddspan.Span]],
    ) -> None:
        """Seed or update attribution for a native-tracked logical execution context."""
        if not _span_linking_enabled:
            return
        target = LogicalSpanTarget(domain, logical_id)
        span_info = _span_info(span)
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
        linked_span = task_context.get(_active_span_link) if task_context is not None else _active_span_link.get()
        target = LogicalSpanTarget(domain, logical_id)
        if linked_span is None or linked_span.generation != _span_link_generation:
            _clear_span(target)
            return False
        source_span = linked_span.span_ref() if linked_span.span_ref is not None else None
        if linked_span.span_ref is not None and (source_span is None or source_span.finished):
            _clear_span(target)
            return False
        _publish_span(target, linked_span.span_info)
        return True

    def clear_logical_span(domain: SpanLinkDomain, logical_id: int) -> None:
        """Clear attribution when a logical execution context is no longer renderable."""
        _stack.clear_logical_span(domain, logical_id)

    def _unlink_finished_span(span: ddspan.Span) -> None:
        """Atomically remove every current target derived from a finished span."""
        if _span_linking_enabled:
            _stack.unlink_finished_span(span.span_id)

    def link_origin_task(task_id: int, task_name: str) -> None:
        """Record the asyncio task that submitted work now running on the current thread."""
        _stack.link_origin_task(task_id, task_name)

    def unlink_origin_task() -> None:
        """Clear the originating asyncio task for the current thread."""
        _stack.unlink_origin_task()

    is_available = True

except Exception as e:
    failure_msg = str(e)
