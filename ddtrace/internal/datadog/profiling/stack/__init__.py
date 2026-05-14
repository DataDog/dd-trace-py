# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    import threading
    import typing

    from ddtrace._trace import context
    from ddtrace._trace import span as ddspan

    from ._stack import *  # noqa: F403, F401

    is_available = True

    # Thread-local storage for the propagated local-root span info.
    #
    # When the futures integration activates a parent Context on a worker thread
    # (tracer._activate_context), link_span stores the context's
    # local_root_span_id here. Subsequent Span activations on the same thread
    # that were created from a propagated Context (span._parent is None but
    # span.parent_id is not None) read this value instead of span._local_root,
    # which would otherwise point to the child span itself and lose the
    # distributed-trace root association
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
            _stack.link_span(span_id, local_root_span_id, local_root_span_type)  # type: ignore # noqa: F405
        elif isinstance(span, context.Context) and span.span_id is not None:
            # When the futures integration propagates a parent trace to a worker
            # thread through tracer._activate_context(), context_provider.activate()
            # fires with a Context, not a Span
            #
            # _wrap_submit attaches _local_root_span_id to the Context
            # from the submitting span so we get the true distributed root.
            # Fall back to span_id when the attribute is absent (e.g. the Context
            # came from somewhere other than the futures integration).
            local_root_span_id = getattr(span, "_local_root_span_id", span.span_id)
            span_type = getattr(span, "_span_type", None)
            # Store in thread-local so child Spans created on this thread can
            # inherit the correct distributed root.
            _propagated_root.span_id = local_root_span_id
            _propagated_root.span_type = span_type
            _stack.link_span(span.span_id, local_root_span_id, span_type)  # type: ignore # noqa: F405

except Exception as e:
    failure_msg = str(e)
