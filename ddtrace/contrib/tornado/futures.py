from ddtrace import tracer
from ddtrace.context import Context


def _wrap_submit(func, instance, args, kwargs):
    """
    Wrap `Executor` method used to submit a work executed in another
    thread. This wrapper ensures that a new `Context` is created and
    properly propagated using an intermediate function.
    """
    # create a new Context with the right active Span
    # TODO: the current implementation doesn't provide the GlobalTracer
    # singleton, so we should rely in our top-level import
    ctx = Context()
    current_ctx = tracer.context_provider.active()
    if current_ctx is not None:
        ctx._current_span = current_ctx.get_current_span()
        ctx._parent_trace_id = current_ctx.trace_id
        ctx._parent_span_id = current_ctx.span_id
        ctx._sampled = current_ctx.sampled
        ctx._sampling_priority = current_ctx.sampling_priority

    # extract the target function that must be executed in
    # a new thread and the `target` arguments
    fn = args[0]
    fn_args = args[1:]
    return func(_wrap_execution, ctx, fn, fn_args, kwargs)

def _wrap_execution(ctx, fn, args, kwargs):
    """
    Intermediate target function that is executed in a new thread;
    it receives the original function with arguments and keyword
    arguments, including our tracing `Context`. The current context
    provider sets the Active context in a thread local storage
    variable because it's outside the asynchronous loop.
    """
    tracer.context_provider.activate(ctx)
    return fn(*args, **kwargs)
