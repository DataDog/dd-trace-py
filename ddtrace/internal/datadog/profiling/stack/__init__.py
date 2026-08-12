# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    import typing

    from ddtrace._trace import context
    from ddtrace._trace import span as ddspan
    from ddtrace.internal.datadog.profiling import context_meta

    from . import _stack
    from ._stack import *  # noqa: F403, F401  # type: ignore[assignment]

    is_available = True

    def link_span(span: typing.Optional[typing.Union[context.Context, ddspan.Span]]) -> None:
        if isinstance(span, ddspan.Span):
            span_id = span.span_id
            # A Span whose _parent is None but parent_id is set was created with child_of=Context. Its local root is
            # the new span, so read the distributed local-root metadata directly from the parent Context. This works
            # across both thread and greenlet context propagation without relying on physical-thread-local state.
            if span._parent is None and span.parent_id is not None and span._parent_context is not None:
                propagated_root_span_id, propagated_root_span_type = context_meta.read_profiler_link(
                    span._parent_context
                )
                local_root_span_id = propagated_root_span_id or span._local_root.span_id
                local_root_span_type = propagated_root_span_type or span._local_root.span_type
            else:
                local_root_span_id = span._local_root.span_id
                local_root_span_type = span._local_root.span_type
            _stack.link_span(span_id, local_root_span_id, local_root_span_type)
        elif isinstance(span, context.Context) and span.span_id is not None:
            local_root_span_id, span_type = context_meta.read_profiler_link(span)
            _stack.link_span(span.span_id, local_root_span_id, span_type)

    def link_origin_task(task_id: int, task_name: str) -> None:
        """
        Record, for the current thread, the asyncio task that submitted the work now running on it.
        """
        _stack.link_origin_task(task_id, task_name)

    def unlink_origin_task() -> None:
        """
        Clear the originating asyncio task for the current thread.
        """
        _stack.unlink_origin_task()

except Exception as e:
    failure_msg = str(e)
