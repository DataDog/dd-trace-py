from .stack_context import ContextManager

from ...tracer import Tracer


class TornadoContextMixin(object):
    """
    Defines by composition how to retrieve the ``Context`` object, while
    running the tracer in a Tornado web application. It handles the Context
    switching only when using the default ``IOLoop``.
    """
    def get_call_context(self):
        """
        Returns the ``Context`` for this execution flow wrapped inside
        a ``StackContext``. The automatic use of a ``ContextManager``
        doesn't handle the context switching when a delayed callback
        is scheduled. In that case, the reference of the current active
        context must be handled manually.
        """
        return ContextManager.current_context()


class TornadoTracer(TornadoContextMixin, Tracer):
    """
    ``TornadoTracer`` is used to create, sample and submit spans that measure the
    execution time of sections of asynchronous Tornado code.

    TODO: this Tracer must not be used directly and this docstring will be removed.
    """
    pass
