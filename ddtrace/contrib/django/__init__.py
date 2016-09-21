"""
The Django integration will trace requests, database calls and template
renders.

To install the Django tracing middleware, add it to the list of your
installed apps and in your middleware classes in ``settings.py``::

    INSTALLED_APPS = [
        # your Django apps...

        # the order is not important
        'ddtrace.contrib.django',
    ]

    MIDDLEWARE_CLASSES = (
        # the tracer must be the first middleware
        'ddtrace.contrib.django.TraceMiddleware',

        # your middleware...
    )

The configuration of this integration is all namespaced inside a single
Django setting, named ``DATADOG_TRACE``. For example, your ``settings.py``
may contain::

    DATADOG_TRACE = {
        'DEFAULT_SERVICE': 'my-django-app',
    }

If you need to access to the tracing settings, you should::

    from ddtrace.contrib.django.conf import settings

    tracer = settings.TRACER
    tracer.trace("something")
    # your code ...

The available settings are:

* ``TRACER`` (default: ``ddtrace.tracer``): set the default tracer
  instance that is used to trace Django internals. By default the ``ddtrace``
  tracer is used.
* ``DEFAULT_SERVICE`` (default: ``django``): set the service name used by the
  tracer. Usually this configuration must be updated with a meaningful name.
* ``ENABLED``: (default: ``not django_settings.DEBUG``): set if the tracer
  is enabled or not. When a tracer is disabled, Django internals are not
  automatically instrumented and the requests are not traced even if the
  ``TraceMiddleware`` is properly installed. This setting cannot be changed
  at runtime and a restart is required. By default the tracer is disabled
  when in ``DEBUG`` mode, enabled otherwise.
"""
from ..util import require_modules


required_modules = ['django']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware
        __all__ = ['TraceMiddleware']


# define the Django app configuration
default_app_config = 'ddtrace.contrib.django.apps.TracerConfig'
