"""
The Django integration will trace users requests, template renderers, database and cache
calls.
"""
from ...utils.importlib import require_modules


required_modules = ['django']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware, TraceExceptionMiddleware
        from .patch import patch, unpatch
        __all__ = ['TraceMiddleware', 'TraceExceptionMiddleware', 'patch', 'unpatch']


# define the Django app configuration
default_app_config = 'ddtrace.contrib.django.apps.TracerConfig'
