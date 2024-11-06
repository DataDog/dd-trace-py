# See ../ddup/__init__.py for some discussion on the is_available attribute.
# This component is also loaded in ddtrace/settings/profiling.py
is_available = False
failure_msg = ""


try:
    from ddtrace._trace import span as ddspan

    from ._stack_v2 import *  # noqa: F403, F401

    is_available = True

    def link_span(span: ddspan.Span) -> None:
        root = span._local_root
        _stack_v2.link_span(span.span_id, root.span_id, root.span_type)  # type: ignore # noqa: F405

except Exception as e:
    failure_msg = str(e)
