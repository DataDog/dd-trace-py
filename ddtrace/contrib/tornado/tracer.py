from .stack_context import ContextManager

from ...tracer import Tracer
from ...context import Context

class TornadoContextMixin(object):
    """
    TODO
    """
    def get_call_context(self):
        """
        TODO
        """
        return ContextManager.current_context()


class TornadoTracer(TornadoContextMixin, Tracer):
    """
    TODO: usage documentation
    """
    pass
