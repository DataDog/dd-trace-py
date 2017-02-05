import threading

from ...context import Context


class ContextManager(object):
    """
    A context manager that manages Context instances in thread-local state.
    It must be used with the Tornado ``StackContext`` and not alone, because
    it doesn't work in asynchronous environments. To use it within a
    ``StackContext``, simply::

        with StackContext(lambda: ContextManager()):
            ctx = ContextManager.current_context()
            # use your context here
    """

    _state = threading.local()
    _state.context = None

    @classmethod
    def current_context(cls):
        """
        Get the ``Context`` from the current execution flow. This method can be
        used inside Tornado coroutines to retrieve and use the current context.
        At the moment, the method cannot handle ``Context`` switching when
        delayed callbacks are used.
        """
        return getattr(cls._state, 'context', None)

    def __init__(self):
        self._context = Context()

    def __enter__(self):
        """
        Enable a new ``Context`` instance.
        """
        self._prev_context = self.__class__.current_context()
        self.__class__._state.context = self._context
        return self._context

    def __exit__(self, *_):
        """
        Disable the current ``Context`` instance and activate the previous one.
        """
        self.__class__._state.context = self._prev_context
        self._prev_context = None
        return False
