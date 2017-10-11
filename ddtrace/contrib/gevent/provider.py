import gevent

from ...context import Context
from ...provider import BaseContextProvider


# Greenlet attribute used to set/get the Context instance
CONTEXT_ATTR = '__datadog_context'


class GeventContextProvider(BaseContextProvider):
    """
    Context provider that retrieves all contexts for the current asynchronous
    execution. It must be used in asynchronous programming that relies
    in the ``gevent`` library. Framework instrumentation that uses the
    gevent WSGI server (or gevent in general), can use this provider.
    """
    def activate(self, context):
        """Sets the scoped ``Context`` for the current running ``Greenlet``.
        """
        current_g = gevent.getcurrent()
        if current_g is not None:
            setattr(current_g, CONTEXT_ATTR, context)
            return context

    def active(self):
        """
        Returns the scoped ``Context`` for this execution flow. The ``Context``
        uses the ``Greenlet`` class as a carrier, and everytime a greenlet
        is created it receives the "parent" context.
        """
        current_g = gevent.getcurrent()
        ctx = getattr(current_g, CONTEXT_ATTR, None)
        if ctx is not None:
            # return the active Context for this greenlet (if any)
            return ctx

        # the Greenlet doesn't have a Context so it's created and attached
        # even to the main greenlet. This is required in Distributed Tracing
        # when a new arbitrary Context is provided.
        if current_g:
            ctx = Context()
            setattr(current_g, CONTEXT_ATTR, ctx)
            return ctx
