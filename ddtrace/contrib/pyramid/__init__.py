"""To trace requests from a Pyramid application, trace your application
config::


    from pyramid.config import Configurator
    from ddtrace.contrib.pyramid import trace_pyramid

    settings = {
        'datadog_trace_service' : 'my-web-app-name',
    }

    config = Configurator(settings=settings)
    trace_pyramid(config)

    # use your config as normal.
    config.add_route('index', '/')
"""

from ..util import require_modules

required_modules = ['pyramid']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .trace import trace_pyramid, trace_tween_factory
        __all__ = [
            'trace_pyramid',
            'trace_tween_factory',
        ]
