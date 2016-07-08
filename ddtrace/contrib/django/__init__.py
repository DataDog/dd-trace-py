"""
The Django middleware will trace requests, database calls and template
renders.

To install the Django tracing middleware, add it to the list of your
application's installed in middleware in settings.py::


    MIDDLEWARE_CLASSES = (
        ...
        'ddtrace.contrib.django.TraceMiddleware',
        ...
    )

    DATADOG_SERVICE = 'my-app'

"""

from ..util import require_modules

required_modules = ['django']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware
        __all__ = ['TraceMiddleware']
