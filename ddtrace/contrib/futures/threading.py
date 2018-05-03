import ddtrace


def _wrap_submit(func, instance, args, kwargs):
    """
    Wrap `Executor` method used to submit a work executed in another
    thread. This wrapper ensures that a new `Context` is created and
    properly propagated using an intermediate function.
    """
    # propagate the same Context in the new thread
    current_ctx = ddtrace.tracer.context_provider.active()

    # extract the target function that must be executed in
    # a new thread and the `target` arguments
    fn = args[0]
    fn_args = args[1:]
    return func(_wrap_execution, current_ctx, fn, fn_args, kwargs)

def _wrap_execution(ctx, fn, args, kwargs):
    """
    Intermediate target function that is executed in a new thread;
    it receives the original function with arguments and keyword
    arguments, including our tracing `Context`. The current context
    provider sets the Active context in a thread local storage
    variable because it's outside the asynchronous loop.
    """
    ddtrace.tracer.context_provider.activate(ctx)
    return fn(*args, **kwargs)
