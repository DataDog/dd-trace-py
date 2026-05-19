# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    import threading
    import typing

    from ddtrace._trace import context
    from ddtrace._trace import span as ddspan

    from . import _stack
    from ._stack import *  # noqa: F403, F401  # type: ignore[assignment]

    is_available = True

    # Thread-local storage for local-root span info propagated across threads
    _propagated_root: threading.local = threading.local()

    def set_propagated_root(local_root_span_id: int, span_type: typing.Optional[str] = None) -> None:
        """Stash local-root linkage for the current worker thread before Context activation."""
        _propagated_root.span_id = local_root_span_id
        _propagated_root.span_type = span_type

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
            # Context only carries the active span_id (parent). Use thread-local
            # local-root info when the futures integration provided it; otherwise
            # fall back to span_id for both linkage fields. span_type is best-effort.
            local_root_span_id = getattr(_propagated_root, "span_id", None) or span.span_id
            span_type = getattr(_propagated_root, "span_type", None)
            _stack.link_span(span.span_id, local_root_span_id, span_type)

except Exception as e:
    failure_msg = str(e)
