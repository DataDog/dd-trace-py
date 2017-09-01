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
        # TODO: previous implementation avoided to add a Context to the main
        # greenlet because it could have side-effects when switching back
        # and forth between different executions. This results in issues such
        # as: https://github.com/DataDog/dd-trace-py/issues/309
        # and is required for Distributed Tracing when providing a new arbitrary
        # Context. On the other hand, it's imperative to double check if there
        # are side effects.
        if current_g:
            ctx = Context()
            setattr(current_g, CONTEXT_ATTR, ctx)
            return ctx
