# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    import typing

    from ddtrace._trace import context
    from ddtrace._trace import span as ddspan

    from ._stack import *  # noqa: F403, F401

    is_available = True

    def link_span(span: typing.Optional[typing.Union[context.Context, ddspan.Span]]):
        if isinstance(span, ddspan.Span):
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            local_root_span_type = span._local_root.span_type
            _stack.link_span(span_id, local_root_span_id, local_root_span_type)  # type: ignore # noqa: F405
        elif isinstance(span, context.Context) and span.span_id is not None:
            # When the futures integration propagates a parent trace to a worker
            # thread through tracer._activate_context(), context_provider.activate()
            # fires with a Context, not a Span. Without this branch the worker
            # thread would never be linked to any span in the profiler
            #
            # A Context carries trace_id + span_id but not local_root_span_id or
            # span_type, so we use span_id as a best-effort local_root_span_id.
            # If the worker later creates an explicit child span, that
            # Span activation will overwrite this entry with the full information.
            _stack.link_span(span.span_id, span.span_id, None)  # type: ignore # noqa: F405

except Exception as e:
    failure_msg = str(e)
