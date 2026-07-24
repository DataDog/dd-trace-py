# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    import threading
    import typing

    from ddtrace._trace import context
    from ddtrace._trace import span as ddspan
    from ddtrace.internal.datadog.profiling import context_meta

    from . import _stack
    from ._stack import *  # noqa: F403, F401  # type: ignore[assignment]
    from ._stack import _cpu_timer_debug_set_fault_injection  # noqa: F401
    from ._stack import _cpu_timer_debug_stats  # noqa: F401

    is_available = True

    # Thread-local storage for local-root span info read from Context._meta on activation.
    # Child Spans created on the worker from a propagated Context use this value.
    _propagated_root: threading.local = threading.local()

    def link_span(span: typing.Optional[typing.Union[context.Context, ddspan.Span]]):
        if isinstance(span, ddspan.Span):
            span_id = span.span_id
            # A Span whose _parent is None but parent_id is set was created with
            # child_of=Context (cross-thread propagation). In that case _local_root
            # is the span itself, which loses the distributed-trace root.
            # Use the thread-local root stored during Context activation instead.
            if span._parent is None and span.parent_id is not None:
                local_root_span_id = getattr(_propagated_root, "span_id", None) or span._local_root.span_id
                local_root_span_type = getattr(_propagated_root, "span_type", None) or span._local_root.span_type
            else:
                local_root_span_id = span._local_root.span_id
                local_root_span_type = span._local_root.span_type
            _stack.link_span(span_id, local_root_span_id, local_root_span_type)
        elif isinstance(span, context.Context) and span.span_id is not None:
            local_root_span_id, span_type = context_meta.read_profiler_link(span)
            _propagated_root.span_id = local_root_span_id
            _propagated_root.span_type = span_type
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
