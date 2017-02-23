"""
``asyncio`` module hosts the ``AsyncioContextProvider`` that follows the execution
flow of ``Task``, making possible to trace asynchronous code built on top
of ``asyncio``. To enable the provider, in your code you should::

    from ddtrace import tracer
    from ddtrace.contrib.asyncio import context_provider

    # enable asyncio support
    tracer.configure(context_provider=context_provider)

Many helpers are provided to simplify the ``Context`` data structure handling
while working in ``asyncio``. The following helpers are in place:

    * ``set_call_context(task, ctx)``: attach the context to the given ``Task``
      so that it will be available from the ``tracer.get_call_context()``
    * ``ensure_future(coro_or_future, *, loop=None)``: wrapper for the
      ``asyncio.ensure_future`` that attaches the current context to a new
      ``Task`` instance
    * ``run_in_executor(loop, executor, func, *args)``: wrapper for the
      ``loop.run_in_executor`` that attaches the current context to the
      new thread so that the trace can be resumed regardless when
      it's executed
"""
from ..util import require_modules


required_modules = ['asyncio']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .provider import AsyncioContextProvider
        from .helpers import set_call_context, ensure_future, run_in_executor

        context_provider = AsyncioContextProvider()

        __all__ = [
            'context_provider',
            'set_call_context',
            'ensure_future',
            'run_in_executor',
        ]
