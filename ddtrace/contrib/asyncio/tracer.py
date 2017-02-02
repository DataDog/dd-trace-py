import asyncio

from ...tracer import Tracer
from ...context import Context


class AsyncContextMixin(object):
    """
    Defines by composition how to retrieve the ``Context`` object, while
    running the tracer in an asynchronous mode with ``asyncio``.
    """
    def get_call_context(self, loop=None):
        """
        Returns the scoped Context for this execution flow. The ``Context`` uses
        the current task as a carrier so if a single task is used for the entire application,
        the context must be handled separately.
        """
        # TODO: this may raise exceptions; provide defaults or
        # gracefully "log" errors
        loop = loop or asyncio.get_event_loop()

        # the current unit of work (if tasks are used)
        task = asyncio.Task.current_task(loop=loop)
        if task is None:
            # FIXME: this will not work properly in all cases
            # if the Task is None, the application will crash with unhandled exception
            # if we return a Context(), we will attach the trace to a (probably) wrong Context
            return
        try:
            # return the active Context for this task (if any)
            return task.__datadog_context
        except (KeyError, AttributeError):
            # create a new Context using the Task as a Context carrier
            # TODO: we may not want to create Context everytime
            ctx = Context()
            task.__datadog_context = ctx
            return ctx

    def set_call_context(self, task, ctx):
        """
        Updates the Context for the given Task. Useful when you need to
        pass the context among different tasks.
        """
        task.__datadog_context = ctx


class AsyncioTracer(AsyncContextMixin, Tracer):
    """
    ``AsyncioTracer`` is used to create, sample and submit spans that measure the
    execution time of sections of ``asyncio`` code.

    If you're running an application that will serve a single trace per ``Task`` during
    a coroutine execution, you can use the global tracer instance:

    >>> from ddtrace.contrib.asyncio import tracer
    >>> trace = tracer.trace("app.request", "web-server").finish()

    TODO: this docstring must be changed because with asynchronous code users may need
    to pass the context manually, except when using ensure_future() to create new
    execution Task. We must collect more details about common and corner cases usage.
    """
    pass
