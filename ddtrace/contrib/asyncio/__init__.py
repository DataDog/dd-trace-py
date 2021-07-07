"""
This integration provides automatic instrumentation to trace the execution flow
of concurrent execution of ``asyncio.Task``. It also provides a legacy context
provider for supporting tracing of asynchronous execution in Python < 3.7.

For asynchronous execution tracing to work properly a tracer must be configured
with the asyncio context provider (note that the global tracer will already be
configured)::

    import asyncio
    from ddtrace import tracer
    from ddtrace.contrib.asyncio import context_provider

    # enable asyncio support
    tracer.configure(context_provider=context_provider)

    async def some_work():
        with tracer.trace('asyncio.some_work'):
            # do something

    # launch your coroutines as usual
    loop = asyncio.get_event_loop()
    loop.run_until_complete(some_work())
    loop.close()
"""
from ...utils.importlib import require_modules


required_modules = ["asyncio"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .provider import AsyncioContextProvider

        context_provider = AsyncioContextProvider()

        from .helpers import ensure_future
        from .helpers import run_in_executor
        from .helpers import set_call_context
        from .patch import patch

        __all__ = ["context_provider", "set_call_context", "ensure_future", "run_in_executor", "patch"]
