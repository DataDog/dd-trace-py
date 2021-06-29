from typing import Dict
from typing import Optional
from typing import Union

from ddtrace.context import Context
from ddtrace.provider import BaseContextProvider
from ddtrace.span import Span


class TestContextProvider(BaseContextProvider):
    """An implementation of a ContextProvider which provides explicit context
    management through the public attributes `executor_id` and `executor_state`.
    """

    # The current executor that this provider is in. Change as needed to
    # simulate other executors.
    executor_id = 0  # type: int

    # Simulate executor-specific state. This corresponds to thread-locals,
    # task-locals, etc.
    executor_state = {}  # type: Dict[int, Optional[Union[Context, Span]]]

    def _has_active_context(self):
        # type: () -> bool
        return self.active() is not None

    def active(self):
        # type: () -> Optional[Union[Context, Span]]
        return self.executor_state.get(self.executor_id)

    def activate(self, ctx):
        # type: (Optional[Union[Context, Span]]) -> None
        self.executor_state[self.executor_id] = ctx

    def _executor_id(self):
        # type: () -> int
        return self.executor_id


def test_default_cross_execution_parenting_multi_span(tracer, test_spans):
    """
    When an active trace is continued in a new executor
        Spans created in the new executor should inherit from the previous executor.
    """

    provider = TestContextProvider()
    tracer.configure(context_provider=provider)

    main_span = tracer.trace("main")

    # Emulate execution going to a new executor (like a thread or task).
    provider.executor_id = 1
    # Emulate the context being copied from the previous executor to the new
    # one.
    # This should be handled by contextvars (asyncio), monkeypatches (gevent)
    # or explicit management (threads).
    provider.activate(main_span.context)

    # New executor begins executing.
    with tracer.trace("new_executor_1"):
        pass
    with tracer.trace("new_executor_2"):
        pass

    # Execution returns to main executor.
    main_span.finish()

    main_span, executor_span1, executor_span2 = test_spans.pop()
    assert main_span.trace_id == executor_span1.trace_id
    assert main_span.trace_id == executor_span2.trace_id
    assert executor_span1.parent_id == main_span.span_id
    assert executor_span2.parent_id == main_span.span_id


def test_default_cross_execution_parent_finish_before_child(tracer, test_spans):
    """ """

    provider = TestContextProvider()
    tracer.configure(context_provider=provider)

    main_span = tracer.trace("main")

    # Emulate execution going to a new executor (like a thread or task).
    provider.executor_id = 1
    # Emulate the context being copied from the previous executor to the new
    # one.
    # This should be handled by contextvars (asyncio), monkeypatches (gevent)
    # or explicit management (threads).
    provider.activate(main_span.context)

    # New executor begins executing.
    with tracer.trace("new_executor"):
        pass

    # Execution returns to main executor.
    main_span.finish()

    main_span, executor_span = test_spans.pop()
    assert main_span.trace_id == executor_span.trace_id
    assert executor_span.parent_id == main_span.span_id
