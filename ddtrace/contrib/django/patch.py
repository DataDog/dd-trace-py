import wrapt

import django

def patch():
    """Patch the instrumented methods
    """
    if getattr(django, '_datadog_patch', False):
        return
    setattr(django, '_datadog_patch', True)

    _w = wrapt.wrap_function_wrapper
    _w('django', 'setup', traced_setup)

def traced_setup(wrapped, instance, args, kwargs):
    from django.conf import settings

    settings.INSTALLED_APPS.append('ddtrace.contrib.django')
    settings.MIDDLEWARE_CLASSES.insert(0, 'ddtrace.contrib.django.TraceMiddleware')
    wrapped(*args, **kwargs)
