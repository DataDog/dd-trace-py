from threading import Timer

from .buffer import TraceBuffer


class AsyncTransport(object):
    """
    Asynchronous worker that executes a ``Send()`` operation after given
    seconds. When the execution is completed, it will reschedule it self so
    that the function is executed forever. You can stop the current
    execution calling the ``stop()`` method.

    :param interval: Time in seconds before each function execution
    :param send: The send function that must be executed
    :param transport: The ``Transport`` instance used for the delivery
    :param buffer_size: The size of internal buffer before starting to discard traces
    """
    def __init__(self, interval, send, transport, buffer_size):
        self._send = send
        self._interval = interval
        self._buffer = TraceBuffer(maxsize=buffer_size)
        self._transport = transport

        self._timer = None

    def callback(self):
        """
        Callback function that executes the ``send()`` method. After the exeuction,
        it should reschedule itself.
        """
        if not self._buffer.empty():
            # we call the send method only if we have a real payload
            # to send, otherwise reschedule it later.
            items = self._buffer.pop()
            self._send(items, self._transport)

        self.start()

    def stop(self):
        """
        Stop the running thread. This action will block the current thread
        immediately.
        """
        self._timer.cancel()

    def start(self):
        """
        Start the timer execution in daemon mode so that when the main thread
        is closed, all active timers and executions will be interrupted immediately.
        """
        self._timer = Timer(self._interval, self.callback)
        self._timer.daemon = True
        self._timer.start()

    def join(self):
        """
        Wait for the thread state to be gone.
        """
        self._timer.join(timeout=10)

    def queue(self, item):
        """
        Enqueue an item in the internal buffer. This operation is thread-safe
        because uses the ``TraceBuffer`` data structure.
        """
        self._buffer.push(item)
