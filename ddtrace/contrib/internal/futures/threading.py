from typing import Optional

import ddtrace
from ddtrace.internal import core
from ddtrace.trace import Context


# Optional profiler linkage metadata passed to worker threads alongside the
# propagated Context
_ProfilerLink = tuple[Optional[int], Optional[str]]


def _wrap_submit(func, args, kwargs):
    """
    Wrap `Executor` method used to submit a work executed in another
    thread. This wrapper ensures that a new `Context` is created and
    properly propagated using an intermediate function.
    """
    # DEV: Be sure to propagate a Context and not a Span since we are crossing thread boundaries
    current_ctx: Optional[Context] = ddtrace.tracer.current_trace_context()
    llmobs_ctx: Optional[Context] = core.dispatch_with_results(  # ast-grep-ignore: core-dispatch-with-results
        "threading.submit", ()
    ).llmobs_ctx.value

    profiler_link: _ProfilerLink = (None, None)
    # Shallow copy for cross-thread handoff. current_trace_context() returns the
    # active Context (or the current span's .context), not a detached copy.
    if current_ctx is not None and current_ctx.trace_id is not None and current_ctx.span_id is not None:
        current_ctx = current_ctx.copy(current_ctx.trace_id, current_ctx.span_id)
        current_span = ddtrace.tracer.current_span()
        if current_span is not None:
            profiler_link = (current_span._local_root.span_id, current_span._local_root.span_type)

    # The target function can be provided as a kwarg argument "fn" or the first positional argument
    self = args[0]
    if "fn" in kwargs:
        fn = kwargs.pop("fn")
        fn_args = args[1:]
    else:
        fn, fn_args = args[1], args[2:]
    return func(self, _wrap_execution, (current_ctx, llmobs_ctx, profiler_link), fn, fn_args, kwargs)


def _wrap_execution(ctx: tuple[Optional[Context], Optional[Context], _ProfilerLink], fn, args, kwargs):
    """
    Intermediate target function that is executed in a new thread;
    it receives the original function with arguments and keyword
    arguments, including our tracing `Context`. The current context
    provider sets the Active context in a thread local storage
    variable because it's outside the asynchronous loop.
    """
    current_ctx, llmobs_ctx, profiler_link = ctx
    local_root_span_id, span_type = profiler_link
    if local_root_span_id is not None:
        from ddtrace.internal.datadog.profiling import stack as stack_module

        if stack_module.is_available:
            stack_module.set_propagated_root(local_root_span_id, span_type)

    if llmobs_ctx is not None:
        core.dispatch("threading.execution", (llmobs_ctx,))
    if current_ctx is not None:
        with ddtrace.tracer._activate_context(current_ctx):
            return fn(*args, **kwargs)
    return fn(*args, **kwargs)
