
import threading


class SpanBuffer(object):
    """ Buffer is an interface for storing the current active span. """

    def set(self, span):
        raise NotImplementedError()

    def get(self):
        raise NotImplementedError()





class ThreadLocalSpanBuffer(object):
    """ ThreadLocalBuffer stores the current active span in thread-local
        storage.
    """

    def __init__(self):
        self._spans = threading.local()

    def set(self, span):
        self._spans.span = span

    def get(self):
        return getattr(self._spans, 'span', None)

    def pop(self):
        span = self.get()
        self.set(None)
        return span

