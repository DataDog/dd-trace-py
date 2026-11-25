"""
async tracing utils

Note that this module should only be imported in Python 3.5+.
"""

from ddtrace.contrib.internal.trace_utils import is_tracing_enabled


def with_traced_module(func):
    """Async version of trace_utils.with_traced_module.
    Usage::

        @with_traced_module
        async def my_traced_wrapper(django, func, instance, args, kwargs):
            # Do tracing stuff
            pass

        def patch():
            import django
            wrap(django.somefunc, my_traced_wrapper(django))
    """

    def with_mod(mod):
        async def wrapper(wrapped, instance, args, kwargs):
            if not is_tracing_enabled():
                return await wrapped(*args, **kwargs)
            return await func(mod, wrapped, instance, args, kwargs)

        return wrapper

    return with_mod
