"""
To trace the falcon web framework, install the trace middleware::

    import falcon
    from ddtrace import tracer
    from ddtrace.contrib.falcon import TraceMiddleware

    mw = TraceMiddleware(tracer, 'my-falcon-app')
    falcon.API(middleware=[mw])
"""
from ..util import require_modules

required_modules = ['falcon']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware
        from .patch import patch

        __all__ = ['TraceMiddleware', 'patch']
