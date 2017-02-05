"""
``asyncio`` module hosts the ``AsyncioTracer`` that is capable to follow
the execution flow of ``Task``, making possible to trace async
code without ``Context`` passing. The public API is the same for the
``Tracer`` class::

    >>> from ddtrace.contrib.asyncio import tracer
    >>> trace = tracer.trace("app.request", "web-server").finish()

Helpers are provided to enforce ``Context`` passing when new threads or
``Task`` are detached from the main execution flow.
"""
from .tracer import AsyncioTracer


# a global asyncio tracer instance
tracer = AsyncioTracer()
