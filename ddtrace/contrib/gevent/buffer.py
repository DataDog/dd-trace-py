import gevent.local
from ddtrace.buffer import SpanBuffer

class GreenletLocalSpanBuffer(SpanBuffer):
    """ GreenletLocalSpanBuffer stores the current active span in greenlet-local
        storage.
    """

    def __init__(self):
        self._locals = gevent.local.local()

    def set(self, span):
        self._locals.span = span

    def get(self):
        return getattr(self._locals, 'span', None)

    def pop(self):
        span = self.get()
        self.set(None)
        return span
