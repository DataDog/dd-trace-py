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

    if 'ddtrace.contrib.django' not in settings.INSTALLED_APPS:
        if isinstance(settings.INSTALLED_APPS, tuple):
            # INSTALLED_APPS is a tuple < 1.9
            settings.INSTALLED_APPS = settings.INSTALLED_APPS + ('ddtrace.contrib.django', )
        else:
            settings.INSTALLED_APPS.append('ddtrace.contrib.django')

    if hasattr(settings, 'MIDDLEWARE_CLASSES'):
        if 'ddtrace.contrib.django.TraceMiddleware' not in settings.MIDDLEWARE_CLASSES:
            if isinstance(settings.MIDDLEWARE_CLASSES, tuple):
                # MIDDLEWARE_CLASSES is a tuple < 1.9
                settings.MIDDLEWARE_CLASSES = ('ddtrace.contrib.django.TraceMiddleware', ) + settings.MIDDLEWARE_CLASSES
            else:
                settings.MIDDLEWARE_CLASSES.insert(0, 'ddtrace.contrib.django.TraceMiddleware')

    if hasattr(settings, 'MIDDLEWARE'):
        if 'ddtrace.contrib.django.TraceMiddleware' not in settings.MIDDLEWARE:
            settings.MIDDLEWARE.insert(0, 'ddtrace.contrib.django.TraceMiddleware')

    wrapped(*args, **kwargs)
