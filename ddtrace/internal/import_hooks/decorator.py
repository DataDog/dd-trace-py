from .registry import hooks


def register_module_hook(module_name, func=None):
    """
    Register a function as a module import hook

    .. code:: python

        @register_module_hook('requests')
        def requests_hook(requests_module):
            pass


        def requests_hook(requests_module):
            pass


        register_module_hook('requests', requests_hook)


    :param module_name: The name of the module to add a hook for (e.g. 'requests', 'flask.app', etc)
    :type module_name: str
    :param func: The hook function to call when the ``module_name`` is imported
    :type func: function(module)
    :returns: Either a decorator function if ``func`` is not provided, or else the original function
    :rtype: func

    """
    # If they did not give us a function, then return a decorator function
    if not func:
        def wrapper(func):
            return register_module_hook(module_name, func)
        return wrapper

    # Register this function as an import hook
    hooks.register(module_name, func)
    return func
