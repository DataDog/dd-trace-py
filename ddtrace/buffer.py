import random
import threading


class SpanBuffer(object):
    """ Buffer is an interface for storing the current active span. """

    def set(self, span):
        raise NotImplementedError()

    def get(self):
        raise NotImplementedError()


class ThreadLocalSpanBuffer(SpanBuffer):
    """ ThreadLocalSpanBuffer stores the current active span in thread-local
        storage.
    """

    def __init__(self):
        self._locals = threading.local()

    def set(self, span):
        self._locals.span = span

    def get(self):
        return getattr(self._locals, 'span', None)

    def pop(self):
        span = self.get()
        self.set(None)
        return span


class TraceBuffer(object):
    """
    Trace buffer that stores application traces. The buffer has a maximum size and when
    the buffer is full, a random trace is discarded. This class is thread-safe and is used
    automatically by the ``Tracer`` instance when a ``Span`` is finished.
    """
    def __init__(self, maxsize=0):
        """
        Initializes a python Queue with a max value.
        """
        self._maxsize = maxsize
        self._mutex = threading.Lock()
        self._queue = []

    def push(self, trace):
        """
        Add a new ``trace`` in the local queue. This method doesn't block the execution
        even if the buffer is full. In that case, a random trace is discarded.
        """
        with self._mutex:
            if len(self._queue) < self._maxsize or self._maxsize <= 0:
                self._queue.append(trace)
            else:
                # we should replace a random trace with the new one
                index = random.randrange(self._maxsize)
                self._queue[index] = trace

    def size(self):
        """
        Return the current number of stored traces.
        """
        return len(self._queue)

    def empty(self):
        """
        Return if the buffer is empty.
        """
        return len(self._queue) == 0

    def pop(self):
        """
        Stored traces are returned and the local buffer is reset.
        """
        with self._mutex:
            traces, self._queue = self._queue, []

        return traces
