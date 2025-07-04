# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    import typing

    from ddtrace._trace import context
    from ddtrace._trace import span as ddspan

    from ._stack_v2 import *  # noqa: F403, F401

    is_available = True

    def link_span(span: typing.Optional[typing.Union[context.Context, ddspan.Span]]):
        if isinstance(span, ddspan.Span):
            span_id = span.span_id
            # PERF: only access span._local_root once since it is a computed property
            _local_root = span._local_root
            local_root_span_id = _local_root.span_id
            local_root_span_type = _local_root.span_type
            _stack_v2.link_span(span_id, local_root_span_id, local_root_span_type)  # type: ignore # noqa: F405

except Exception as e:
    failure_msg = str(e)
