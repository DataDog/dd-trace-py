import threading
import tornado.stack_context

from ...context import Context


class ContextManager(object):
    """
    A context manager that manages Context instances in thread-local state.
    Intended for use with the ddtrace StackContext and not alone because
    it doesn't work in asynchronous environments.

    TODO: the current implementation sets the active context in the ContextManager
    class. This will not work without the StackContext.
    """

    _state = threading.local()
    _state.context = None

    @classmethod
    def current_context(cls):
        """
        Get the Context from the current execution unit.
        """
        return getattr(cls._state, 'context', None)

    def __init__(self):
        self._context = Context()

    def __enter__(self):
        self._prev_context = self.__class__.current_context()
        self.__class__._state.context = self._context
        return self._context

    def __exit__(self, *_):
        self.__class__._state.context = self._prev_context
        self._prev_context = None
        return False
