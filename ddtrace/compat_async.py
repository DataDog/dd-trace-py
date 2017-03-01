"""
Async compat module that includes all asynchronous syntax that is not
Python 2 compatible. It MUST be used only in the ``compat``
module that owns the logic to import it or not.
"""
import functools
import asyncio


def _make_async_decorator(tracer, coro, *params, **kw_params):
    """
    Decorator factory that creates an asynchronous wrapper that yields
    a coroutine result. This factory is required to handle Python 2
    compatibilities.

    :param object tracer: the tracer instance that is used
    :param function f: the coroutine that must be executed
    :param tuple params: arguments given to the Tracer.trace()
    :param dict kw_params: keyword arguments given to the Tracer.trace()
    """
    @functools.wraps(coro)
    @asyncio.coroutine
    def func_wrapper(*args, **kwargs):
        with tracer.trace(*params, **kw_params):
            result = yield from coro(*args, **kwargs)  # noqa: E999
            return result

    return func_wrapper
