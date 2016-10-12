"""
To trace a request in a gevent-ed environment, patch `threading.local` to make it coroutine-safe. Then make sure to pass down the span object from the parent to coroutine context

    ```
    # Always monkey patch before importing the global tracer
    from gevent import monkey; monkey.patch_thread(_threading_local=True)

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
            ....
    ```
"""
