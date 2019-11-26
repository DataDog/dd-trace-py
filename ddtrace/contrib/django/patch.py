"""
The Django patching works as follows:

Django internals are instrumented via normal `patch()`.

django.apps.registry.Apps.populate is patched to add instrumentation for any
specific Django apps like Django Rest Framework (DRF).
"""

from ddtrace import config, Pin, tracer
from ddtrace.vendor.wrapt import wrap_function_wrapper as wrap
from ddtrace.utils.wrappers import unwrap

from ddtrace.utils.importlib import func_name
from ...internal.logger import get_logger

log = get_logger(__name__)

config._add('django', dict(
    service_name='django',
))


def with_instance_pin(func):
    """Helper to wrap a function wrapper and ensure an enabled pin is available for the `instance`"""
    def with_mod(mod):
        def wrapper(wrapped, instance, args, kwargs):
            pin = Pin._find(instance, mod)
            if pin and not pin.enabled():
                return wrapped(*args, **kwargs)
            return func(mod, pin, wrapped, instance, args, kwargs)
        return wrapper
    return with_mod


@with_instance_pin
def traced_populate(django, pin, func, instance, args, kwargs):
    """django.apps.registry.Apps.populate is the method used to populate all the apps.

    It works in 3 phases:

        - Phase 1: Initializes the app configs and imports the app modules.
        - Phase 2: Imports models modules for each app.
        - Phase 3: runs ready() of each app config.

    If all 3 phases successfully run then `instance.ready` will be `True`.
    """

    # populate() can be called multiple times, we don't want to instrument more than once
    already_ready = instance.ready

    try:
        return func(*args, **kwargs)
    finally:
        if not already_ready:
            log.info('Django instrumentation already installed.')
        elif not instance.ready:
            log.warning('populate() failed skipping instrumentation.')
        # Apps have all been populated. This gives us a chance to analyze any
        # installed apps (like django rest framework) to trace.
        else:
            pass


@with_instance_pin
def traced_middleware(django, pin, func, instance, args, kwargs):
    """
    """
    with pin.tracer.trace('django.middleware', resource=func_name(func)) as span:
        return func(*args, **kwargs)


@with_instance_pin
def traced_load_middleware(django, pin, func, instance, args, kwargs):
    """
    Note: this function is idempotent.
    """
    for mw_path in django.conf.settings.MIDDLEWARE:
        split = mw_path.split('.')
        if len(split) > 1:
            mod = '.'.join(split[:-1])
            attr = split[-1]
            wrap(mod, attr + '.__call__', traced_middleware(django))
    return func(*args, **kwargs)


def _patch(django):
    Pin(service=config.django['service_name']).onto(django)
    wrap(django, 'apps.registry.Apps.populate', traced_populate(django))
    wrap(django, 'core.handlers.base.BaseHandler.load_middleware', traced_load_middleware(django))


def patch():
    import django
    if getattr(django, '_datadog_patch', False):
        return

    _patch(django)

    setattr(django, '_datadog_patch', True)


def _unpatch(django):
    unwrap(django, 'apps.config.Apps.populate')


def unpatch():
    import django
    if not getattr(django, '_datadog_patch', False):
        return

    _unpatch(django)

    setattr(django, '_datadog_patch', False)
