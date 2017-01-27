"""
This module implements the AsyncTracer that should be used
when tracing asynchronous code (i.e. asyncio). This tracer
is only Python 3.5+ compatible for the moment.
"""
from . import aio
from ..tracer import BaseTracer


class AsyncTracer(BaseTracer):
    """
    AsyncTracer is used to create, sample and submit spans that measure the
    execution time of sections of asynchronous code.

    If you're running an application that will serve a single trace during
    a coroutine execution, you can use the global tracer instance:

    >>> from ddtrace.async import tracer
    >>> trace = tracer.trace("app.request", "web-server").finish()

    TODO: this section must be changed a lot because with asynchronous code
    users may need to pass the context manually, except when using ensure_future()
    to create new execution Task. We must collect more details about common and corner
    cases usage.
    """
    def get_call_context(self, loop=None):
        """
        Returns the scoped Context for this execution flow. The Context is bounded
        to the current task so if a single task is used for the entire application,
        the context must be handled separately.

        TODO: we may need to simplify this API
        """
        # using this proxy only to keep asyncio stuff in another module
        return aio.get_call_context()
