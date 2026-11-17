import pytest

from ddtrace.internal import core
from ddtrace.internal.span_bus import find_span_on_context
from ddtrace.internal.span_bus import store_span_on_context


@pytest.mark.parametrize("local_span", [False, True])
def test_find_span_on_context_preserves_span_ownership(tracer, test_spans, local_span):
    with tracer.trace("parent") as parent:
        with core.context_with_data("parent.context") as parent_ctx:
            store_span_on_context(parent_ctx, parent)
            with core.context_with_data("child.context") as ctx:
                if local_span:
                    span = tracer.start_span("local", activate=False)
                    store_span_on_context(ctx, span)
                else:
                    span = None

                assert find_span_on_context(ctx) is span
                assert tracer.current_span() is parent
                assert ctx.get_item("_inner_span") is span
                if span is not None:
                    assert not span.finished
                    span.finish()

    spans = test_spans.pop()
    assert len(spans) == (2 if local_span else 1)
    assert parent in spans
