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

    # It might be MIDDLEWARE instead of MIDDLEWARE_CLASSES for Django 1.10+
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
        'TAGS': {'env': 'production'},
    }

If you need to access to the tracing settings, you should::

    from ddtrace.contrib.django.conf import settings

    tracer = settings.TRACER
    tracer.trace("something")
    # your code ...

The available settings are:

* ``DEFAULT_SERVICE`` (default: ``django``): set the service name used by the
  tracer. Usually this configuration must be updated with a meaningful name.
* ``TAGS`` (default: ``{}``): set global tags that should be applied to all
  spans.
* ``TRACER`` (default: ``ddtrace.tracer``): set the default tracer
  instance that is used to trace Django internals. By default the ``ddtrace``
  tracer is used.
* ``ENABLED`` (default: ``not django_settings.DEBUG``): defines if the tracer is
  enabled or not. If set to false, the code is still instrumented but no spans
  are sent to the trace agent. This setting cannot be changed at runtime
  and a restart is required. By default the tracer is disabled when in ``DEBUG``
  mode, enabled otherwise.
* ``AUTO_INSTRUMENT`` (default: ``True``): if set to false the code will not be
  instrumented, while the tracer may be active for your internal usage. This could
  be useful if you want to use the Django integration, but you want to trace only
  particular functions or views. If set to False, the request middleware will be
  disabled even if present.
* ``AGENT_HOSTNAME`` (default: ``localhost``): define the hostname of the trace agent.
* ``AGENT_PORT`` (default: ``8126``): define the port of the trace agent.
"""
from ..util import require_modules


required_modules = ['django']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .middleware import TraceMiddleware
        from .patch import patch
        __all__ = ['TraceMiddleware', 'patch']


# define the Django app configuration
default_app_config = 'ddtrace.contrib.django.apps.TracerConfig'
