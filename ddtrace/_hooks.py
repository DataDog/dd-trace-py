import collections
from copy import deepcopy

from .internal.logger import get_logger

from .vendor import attr

log = get_logger(__name__)


@attr.s(slots=True)
class Hooks(object):
    """
    Hooks configuration object is used for registering and calling hook functions

    Example::

        @config.falcon.hooks.on('request')
        def on_request(span, request, response):
            pass
    """

    _hooks = attr.ib(init=False, factory=lambda: collections.defaultdict(set))

    def __deepcopy__(self, memodict=None):
        hooks = Hooks()
        hooks._hooks = deepcopy(self._hooks, memodict)
        return hooks

    def register(self, hook, func=None):
        """
        Function used to register a hook for the provided name.

        Example::

            def on_request(span, request, response):
                pass

            config.falcon.hooks.register('request', on_request)


        If no function is provided then a decorator is returned::

            @config.falcon.hooks.register('request')
            def on_request(span, request, response):
                pass

        :param hook: The name of the hook to register the function for
        :type hook: object
        :param func: The function to register, or ``None`` if a decorator should be returned
        :type func: function, None
        :returns: Either a function decorator if ``func is None``, otherwise ``None``
        :rtype: function, None
        """
        # If they didn't provide a function, then return a decorator
        if not func:

            def wrapper(func):
                self.register(hook, func)
                return func

            return wrapper
        self._hooks[hook].add(func)

    # Provide shorthand `on` method for `register`
    # >>> @config.falcon.hooks.on('request')
    #     def on_request(span, request, response):
    #        pass
    on = register

    def deregister(self, hook, func):
        """
        Function to deregister a function from a hook it was registered under

        Example::

            @config.falcon.hooks.on('request')
            def on_request(span, request, response):
                pass

            config.falcon.hooks.deregister('request', on_request)

        :param hook: The name of the hook to register the function for
        :type hook: object
        :param func: Function hook to register
        :type func: function
        """
        if hook in self._hooks:
            try:
                self._hooks[hook].remove(func)
            except KeyError:
                pass

    def emit(self, hook, *args, **kwargs):
        """
        Function used to call registered hook functions.

        :param hook: The hook to call functions for
        :type hook: str
        :param args: Positional arguments to pass to the hook functions
        :type args: list
        :param kwargs: Keyword arguments to pass to the hook functions
        :type kwargs: dict
        """
        # Call registered hooks
        for func in self._hooks.get(hook, ()):
            try:
                func(*args, **kwargs)
            except Exception:
                log.error("Failed to run hook %s function %s", hook, func, exc_info=True)
