"""
``asyncio`` module hosts the ``AsyncioTracer`` that follows the execution
flow of ``Task``, making possible to trace asynchronous code without
``Context`` passing. The public API is the same of the ``Tracer`` class::

    >>> from ddtrace.contrib.asyncio import tracer
    >>> trace = tracer.trace("app.request", "web-server").finish()

Helpers are provided to enforce ``Context`` passing when new threads or
``Task`` are detached from the main execution flow.
"""
from ..util import require_modules

required_modules = ['asyncio']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .tracer import AsyncioTracer

        # a global asyncio tracer instance
        # TODO: this must be removed when we have a clean API
        tracer = AsyncioTracer()

        from .helpers import ensure_future, run_in_executor

        __all__ = [
            'tracer',
            'ensure_future',
            'run_in_executor',
        ]
