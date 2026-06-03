from typing import Optional

import ddtrace
from ddtrace.internal import core
from ddtrace.internal.datadog.profiling import context_meta
from ddtrace.trace import Context


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

    # Shallow copy for cross-thread handoff. current_trace_context() returns the
    # active Context (or the current span's .context), not a detached copy.
    if current_ctx is not None and current_ctx.trace_id is not None and current_ctx.span_id is not None:
        current_ctx = current_ctx.copy(current_ctx.trace_id, current_ctx.span_id)
        current_span = ddtrace.tracer.current_span()
        if current_span is not None:
            context_meta.attach_profiler_link(
                current_ctx,
                current_span._local_root.span_id,
                current_span._local_root.span_type,
            )

    # The target function can be provided as a kwarg argument "fn" or the first positional argument
    self = args[0]
    if "fn" in kwargs:
        fn = kwargs.pop("fn")
        fn_args = args[1:]
    else:
        fn, fn_args = args[1], args[2:]
    return func(self, _wrap_execution, (current_ctx, llmobs_ctx), fn, fn_args, kwargs)


def _wrap_execution(ctx: tuple[Optional[Context], Optional[Context]], fn, args, kwargs):
    """
    Intermediate target function that is executed in a new thread;
    it receives the original function with arguments and keyword
    arguments, including our tracing `Context`. The current context
    provider sets the Active context in a thread local storage
    variable because it's outside the asynchronous loop.
    """
    current_ctx, llmobs_ctx = ctx
    if llmobs_ctx is not None:
        core.dispatch("threading.execution", (llmobs_ctx,))
    if current_ctx is not None:
        with ddtrace.tracer._activate_context(current_ctx):
            return fn(*args, **kwargs)
    return fn(*args, **kwargs)
