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
        try:
            loop = loop or asyncio.get_event_loop()
        except RuntimeError:
            # handles RuntimeError: There is no current event loop in thread 'MainThread'
            # it happens when it's not possible to get the current event loop.
            # It's possible that a different Executor is handling a different Thread that
            # works with blocking code. In that case, we fallback to a thread-local Context.
            return self._context.get()

        # the current unit of work (if tasks are used)
        task = asyncio.Task.current_task(loop=loop)
        if task is None:
            # providing a detached Context from the current Task, may lead to
            # wrong traces. This defensive behavior grants that a trace can
            # still be built without raising exceptions
            return Context()

        ctx = getattr(task, '__datadog_context', None)
        if ctx is not None:
            # return the active Context for this task (if any)
            return ctx

        # create a new Context using the Task as a Context carrier
        ctx = Context()
        setattr(task, '__datadog_context', ctx)
        return ctx

    def set_call_context(self, task, ctx):
        """
        Updates the Context for the given Task. Useful when you need to
        pass the context among different tasks.
        """
        setattr(task, '__datadog_context', ctx)


class AsyncioTracer(AsyncContextMixin, Tracer):
    """
    ``AsyncioTracer`` is used to create, sample and submit spans that measure the
    execution time of sections of ``asyncio`` code.

    TODO: this Tracer must not be used directly and this docstring will be removed.
    """
    pass
