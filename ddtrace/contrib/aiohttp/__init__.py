"""
The ``aiohttp`` integration traces all requests defined in the application handlers.
Auto instrumentation is available using the ``trace_app`` function::

    from aiohttp import web
    from ddtrace import tracer, patch
    from ddtrace.contrib.aiohttp import trace_app

    # patch third-party modules like aiohttp_jinja2
    patch(aiohttp=True)

    # create your application
    app = web.Application()
    app.router.add_get('/', home_handler)

    # trace your application handlers
    trace_app(app, tracer, service='async-api')
    web.run_app(app, port=8000)

Third-party modules that are currently supported by the ``patch()`` method are:

* ``aiohttp_jinja2``

When a request span is automatically created, the ``Context`` for this logical execution
is attached to the ``request`` object, so that it can be used in the application code::

    async def home_handler(request):
        ctx = request['datadog_context']
        # do something with the tracing Context
"""
from ..util import require_modules

required_modules = ['aiohttp']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch
        from .middlewares import trace_app

        __all__ = [
            'patch',
            'unpatch',
            'trace_app',
        ]
