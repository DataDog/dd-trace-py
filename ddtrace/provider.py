from .context import ThreadLocalContext


class BaseContextProvider(object):
    """
    A ``ContextProvider`` is an interface that provides the blueprint
    for a callable class, capable to retrieve the current active
    ``Context`` instance. Context providers must inherit this class
    and implement:
        * the ``__call__`` method, so that the class is callable
    """

    def __call__(self, *args, **kwargs):
        """
        Makes the class callable so that the ``Tracer`` can invoke the
        ``ContextProvider`` to retrieve the current context.
        This class must be implemented.
        """
        raise NotImplementedError


class DefaultContextProvider(BaseContextProvider):
    """
    Default context provider that retrieves all contexts from the current
    thread-local storage. It is suitable for synchronous programming and
    Python WSGI frameworks.
    """
    def __init__(self):
        self._local = ThreadLocalContext()

    def __call__(self, *args, **kwargs):
        """
        Returns the global context for this tracer. Returned ``Context`` must be thread-safe
        or thread-local.
        """
        return self._local.get()
