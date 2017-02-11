"""
The ``aiohttp`` integration traces all requests received by defined routes
and handlers. External modules for database calls and templates rendering
are not automatically instrumented, so you must use the ``patch()`` function::

    from aiohttp import web
    from ddtrace import tracer, patch
    from ddtrace.contrib.aiohttp.middlewares import TraceMiddleware

    # patch external modules like aiohttp_jinja2
    patch(aiohttp=True)

    # create your application
    app = web.Application()
    app.router.add_get('/', home_handler)

    # add the tracing middleware
    TraceMiddleware(app, tracer, service='async-api')
    web.run_app(app, port=8000)
"""
from ..util import require_modules

required_modules = ['aiohttp']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch, unpatch
        from .middlewares import TraceMiddleware

        __all__ = [
            'patch',
            'unpatch',
            'TraceMiddleware',
        ]
