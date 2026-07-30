# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    import contextvars
    import typing

    from ddtrace._trace import context
    from ddtrace._trace import span as ddspan
    from ddtrace.internal import forksafe
    from ddtrace.internal.datadog.profiling import context_meta

    from . import _stack
    from ._stack import *  # noqa: F403, F401  # type: ignore[assignment]

    class _SpanInfo(typing.NamedTuple):
        span_id: int
        local_root_span_id: int
        span_type: typing.Optional[str]

    class _SpanLinkTarget(typing.NamedTuple):
        is_logical: bool
        identifier: int

    class _SpanLinkContext(typing.NamedTuple):
        generation: int
        span_info: _SpanInfo

    _LogicalSpanProvider = typing.Callable[[], typing.Optional[int]]

    _span_linking_enabled = False
    _span_link_generation = 0
    _active_span_link: contextvars.ContextVar[typing.Optional[_SpanLinkContext]] = contextvars.ContextVar(
        "ddtrace_profiling_active_span_link", default=None
    )
    _logical_span_providers: list[tuple[int, _LogicalSpanProvider]] = []
    _target_spans: dict[_SpanLinkTarget, _SpanInfo] = {}
    _span_targets: dict[int, set[_SpanLinkTarget]] = {}

    def _reset_span_link_state() -> None:
        _target_spans.clear()
        _span_targets.clear()

    def enable_span_linking() -> None:
        global _span_link_generation
        global _span_linking_enabled

        _reset_span_link_state()
        _span_link_generation += 1
        _active_span_link.set(None)
        _span_linking_enabled = True

    def disable_span_linking() -> None:
        global _span_linking_enabled

        _span_linking_enabled = False
        _active_span_link.set(None)
        _reset_span_link_state()

    def register_logical_span_provider(provider: _LogicalSpanProvider, priority: int = 0) -> None:
        """Register a non-owning resolver for the current logical execution context."""
        if any(registered is provider for _, registered in _logical_span_providers):
            return
        _logical_span_providers.append((priority, provider))
        _logical_span_providers.sort(key=lambda item: item[0], reverse=True)

    def unregister_logical_span_provider(provider: _LogicalSpanProvider) -> None:
        """Stop consulting a logical execution-context resolver."""
        _logical_span_providers[:] = [
            (priority, registered) for priority, registered in _logical_span_providers if registered is not provider
        ]

    def _current_span_link_target() -> _SpanLinkTarget:
        for _, provider in _logical_span_providers:
            try:
                logical_id = provider()
            except Exception:  # nosec B112
                continue
            if logical_id is not None:
                return _SpanLinkTarget(True, logical_id)
        return _SpanLinkTarget(False, _stack.get_thread_id())

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

    def _remove_target_registration(target: _SpanLinkTarget) -> typing.Optional[_SpanInfo]:
        span_info = _target_spans.pop(target, None)
        if span_info is None:
            return None
        for indexed_span_id in {span_info.span_id, span_info.local_root_span_id}:
            targets = _span_targets.get(indexed_span_id)
            if targets is None:
                continue
            targets.discard(target)
            if not targets:
                _span_targets.pop(indexed_span_id, None)
        return span_info

    def _record_target_span(target: _SpanLinkTarget, span_info: _SpanInfo) -> None:
        _remove_target_registration(target)
        _target_spans[target] = span_info
        for indexed_span_id in {span_info.span_id, span_info.local_root_span_id}:
            _span_targets.setdefault(indexed_span_id, set()).add(target)

    def _publish_target_span(target: _SpanLinkTarget, span_info: _SpanInfo) -> None:
        if target.is_logical:
            _stack.link_logical_span(
                target.identifier,
                span_info.span_id,
                span_info.local_root_span_id,
                span_info.span_type,
            )
        else:
            _stack.link_span(span_info.span_id, span_info.local_root_span_id, span_info.span_type)
        _record_target_span(target, span_info)

    def _clear_target(target: _SpanLinkTarget) -> None:
        if target.is_logical:
            _stack.clear_logical_span(target.identifier)
        else:
            _stack.clear_span()
        _remove_target_registration(target)

    def _unlink_target(target: _SpanLinkTarget, expected_span_id: int) -> None:
        if target.is_logical:
            _stack.unlink_logical_span(target.identifier, expected_span_id)
        else:
            _stack.unlink_thread_span(target.identifier, expected_span_id)
        _remove_target_registration(target)

    def link_span(span: typing.Optional[typing.Union[context.Context, ddspan.Span]]) -> None:
        """Route a tracing activation to its physical thread or logical execution context."""
        if not _span_linking_enabled:
            return
        target = _current_span_link_target()
        span_info = _span_info(span)
        if span_info is None:
            _active_span_link.set(None)
            _clear_target(target)
        else:
            _active_span_link.set(_SpanLinkContext(_span_link_generation, span_info))
            _publish_target_span(target, span_info)

    def link_logical_span(
        logical_id: int,
        span: typing.Optional[typing.Union[context.Context, ddspan.Span]],
    ) -> None:
        """Seed or update attribution for a known logical execution context."""
        if not _span_linking_enabled:
            return
        target = _SpanLinkTarget(True, logical_id)
        span_info = _span_info(span)
        if span_info is None:
            _clear_target(target)
        else:
            _publish_target_span(target, span_info)

    def link_logical_span_context(logical_id: int, task_context: typing.Optional[contextvars.Context] = None) -> bool:
        """Seed a logical execution context from inherited profiler ContextVar state."""
        if not _span_linking_enabled:
            return False
        linked_span = task_context.get(_active_span_link) if task_context is not None else _active_span_link.get()
        target = _SpanLinkTarget(True, logical_id)
        if linked_span is None or linked_span.generation != _span_link_generation:
            _clear_target(target)
            return False
        _publish_target_span(target, linked_span.span_info)
        return True

    def clear_logical_span(logical_id: int) -> None:
        """Clear attribution when a logical execution context is destroyed."""
        target = _SpanLinkTarget(True, logical_id)
        _stack.clear_logical_span(logical_id)
        _remove_target_registration(target)

    def unlink_finished_span(span: ddspan.Span) -> None:
        """Remove every current attribution derived from a finished span."""
        if not _span_linking_enabled:
            return
        for target in tuple(_span_targets.get(span.span_id, ())):
            span_info = _target_spans.get(target)
            if span_info is not None and span.span_id in (span_info.span_id, span_info.local_root_span_id):
                _unlink_target(target, span_info.span_id)

    def link_origin_task(task_id: int, task_name: str) -> None:
        """Record the asyncio task that submitted work now running on the current thread."""
        _stack.link_origin_task(task_id, task_name)

    def unlink_origin_task() -> None:
        """Clear the originating asyncio task for the current thread."""
        _stack.unlink_origin_task()

    forksafe.register(_reset_span_link_state)
    is_available = True

except Exception as e:
    failure_msg = str(e)
