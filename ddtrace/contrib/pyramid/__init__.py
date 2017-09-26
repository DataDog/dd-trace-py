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

If you use the 'pyramid.tweens' settings value to set the tweens for your
application, you need to add 'ddtrace.contrib.pyramid:trace_tween_factory'
explicitely to the list.

"""

from ..util import require_modules

required_modules = ['pyramid']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .trace import trace_pyramid, trace_tween_factory, includeme
        from .patch import patch

        __all__ = [
            'patch',
            'trace_pyramid',
            'trace_tween_factory',
            'includeme',
        ]
