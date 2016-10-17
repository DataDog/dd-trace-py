"""
To trace a request in a gevent-ed environment, configure the tracer to use greenlet-local
storage, rather than the default thread-local storage.

This allows the tracer to pick up a transaction exactly
where it left off as greenlets yield context to one another.

The simplest way to trace with greenlet-local storage is via the `gevent.monkey` module::

    # Always monkey patch before importing the global tracer
    # Broadly, gevent recommends that patches happen as early as possible in the app lifecycle
    # http://www.gevent.org/gevent.monkey.html#patching

    from gevent import monkey; monkey.patch_thread()
    # Alternatively, use monkey.patch_all() to perform all available patches

    from ddtrace import tracer

    import gevent

    def my_parent_function():
        with tracer.trace("web.request") as span:
            span.service = "web"
            gevent.spawn(worker_function, span)

    def worker_function(parent):
        # Set the active span
        tracer.span_buffer.set(parent)

        # then trace its child
        with tracer.trace("greenlet.call") as span:
            span.service = "greenlet"
            ...

            with tracer.trace("greenlet.child_call") as child:
                ...

Note that when spawning greenlets,
the span object must be explicitly passed from the parent to coroutine context.
A tracer in a freshly-spawned greenlet will not know about its parent span.

If you are unable to patch `gevent` in the global scope, you can configure
the global tracer to use greenlet-local storage on an as-needed basis::

    from ddtrace import tracer
    from ddtrace.contrib.gevent import GreenletLocalSpanBuffer

    import gevent

    def my_parent_function():
        with tracer.trace("web.request") as span:
            span.service = "web"
            gevent.spawn(worker_function, span)

    def worker_function(parent):
        tracer.span_buffer = GreenletLocalSpanBuffer()
        # Set the active span
        tracer.span_buffer.set(parent)

        # then trace its child
        with tracer.trace("greenlet.call") as span:
            span.service = "greenlet"
            ...

            with tracer.trace("greenlet.child_call") as child:
                ...
"""

from ..util import require_modules

required_modules = ['gevent', 'gevent.local']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .buffer import GreenletLocalSpanBuffer
        __all__ = ['GreenletLocalSpanBuffer']
