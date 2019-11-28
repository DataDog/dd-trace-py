"""
The Django patching works as follows:

Django internals are instrumented via normal `patch()`.

django.apps.registry.Apps.populate is patched to add instrumentation for any
specific Django apps like Django Rest Framework (DRF).
"""

from inspect import isclass, isfunction

from ddtrace import config, Pin
from ddtrace.vendor.wrapt import wrap_function_wrapper as wrap, FunctionWrapper
from ddtrace.utils.wrappers import unwrap

from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...contrib import func_name
from ...ext import http
from ...internal.logger import get_logger
from ...propagation.http import HTTPPropagator

from .compat import get_resolver
from .middleware import _django_default_views
from .utils import get_request_uri


log = get_logger(__name__)

config._add('django', dict(
    service_name='django',
    distributed_tracing_enabled=True,
    analytics_enabled=None,  # None allows the value to be overridden by the global config
    analytics_sample_rate=None,
    trace_query_string=None,
))


propagator = HTTPPropagator()


def with_traced_module(func):
    """Helper for providing tracing essentials (module and pin) for tracing wrappers.

    Usage::
        @with_traced_module
        def my_traced_wrapper(mod, pin, func, instance, args, kwargs):
            # Do tracing stuff
            pass

        def patch():
            import django
            wrap(django.somefunc, my_traced_wrapper(django))
    """
    def with_mod(mod):
        def wrapper(wrapped, instance, args, kwargs):
            pin = Pin._find(instance, mod)
            if pin and not pin.enabled():
                return wrapped(*args, **kwargs)
            elif not pin:
                log.warning('Pin not found on traced method')
                return wrapped(*args, **kwargs)
            return func(mod, pin, wrapped, instance, args, kwargs)
        return wrapper
    return with_mod


@with_traced_module
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
            log.info('Django instrumentation already installed, skipping.')
        elif not instance.ready:
            log.warning('populate() failed skipping instrumentation.')
        # Apps have all been populated. This gives us a chance to analyze any
        # installed apps (like django rest framework) to trace.
        else:
            pass


def traced_middleware(django, attr):
    """
    Returns a function to trace class or function based Django middleware.
    """
    def _traced_mw(django, pin, func, instance, args, kwargs):
        with pin.tracer.trace('django.middleware.{}'.format(attr)) as span:
            return func(*args, **kwargs)
    return with_traced_module(_traced_mw)(django)


@with_traced_module
def traced_load_middleware(django, pin, func, instance, args, kwargs):
    """
    """
    for mw_path in django.conf.settings.MIDDLEWARE:
        split = mw_path.split('.')
        if len(split) > 1:
            mod = '.'.join(split[:-1])
            attr = split[-1]
            mw = django.utils.module_loading.import_string(mw_path)
            if isfunction(mw):
                # Function-based middleware is a factory which returns the function used to handle the requests.
                # So instead of wrapping the factory, we want to wrap its returned value.
                def traced_factory(func, instance, args, kwargs):
                    # r is the middleware handler function returned from the factory
                    r = func(*args, **kwargs)
                    return FunctionWrapper(r, traced_middleware(django, mw_path))
                wrap(mod, attr, traced_factory)
            elif isclass(mw):
                if hasattr(mw, 'process_request'):
                    wrap(mod, attr + '.process_request', traced_middleware(django, mw_path + '.process_request'))
                if hasattr(mw, 'process_response'):
                    wrap(mod, attr + '.process_response', traced_middleware(django, mw_path + '.process_response'))
                if hasattr(mw, '__call__'):
                    wrap(mod, attr + '.__call__', traced_middleware(django, mw_path))
    return func(*args, **kwargs)


@with_traced_module
def traced_get_response(django, pin, func, instance, args, kwargs):
    """Trace django.core.handlers.base.BaseHandler.get_response() (or other implementations).

    Django requests are handled by a Handler.get_response method (inherited from base.BaseHandler).
    This method invokes the middleware chain and returns the response generated by the chain.
    """

    request = args[0]

    if config.django['distributed_tracing_enabled']:
        context = propagator.extract(request.META)
        if context.trace_id:
            pin.tracer.context_provider.activate(context)

    response = None
    try:
        with pin.tracer.trace('django.request', service=config.django['service_name'], span_type=http.TYPE) as span:
            # Analytics
            analytics_sr = config.django.get_analytics_sample_rate(use_global_config=True)
            if analytics_sr:
                span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, analytics_sr)

            span.set_tag(http.METHOD, request.method)
            span.set_tag(http.URL, get_request_uri(request))

            if config.django['trace_query_string']:
                span.set_tag(http.QUERY_STRING, request.META['QUERY_STRING'])
            response = func(*args, **kwargs)
            return response
    finally:
        # After the request has finished and a response received, additional data can be retrieved.
        if response:
            span.set_tag(http.STATUS_CODE, response.status_code)

            try:
                # Attempt to lookup the view function from the url resolver
                #   https://github.com/django/django/blob/38e2fdadfd9952e751deed662edf4c496d238f28/django/core/handlers/base.py#L104-L113  # noqa
                urlconf = None
                if hasattr(request, 'urlconf'):
                    urlconf = request.urlconf
                resolver = get_resolver(urlconf)

                # Try to resolve the Django view for handling this request
                if getattr(request, 'request_match', None):
                    request_match = request.request_match
                else:
                    # This may raise a `django.urls.exceptions.Resolver404` exception
                    request_match = resolver.resolve(request.path_info)
                span.resource = func_name(request_match.func)
            except Exception:
                log.debug('error determining request view function', exc_info=True)

                # If the view could not be found, try to set from a static list of
                # known internal error handler views
                span.resource = _django_default_views.get(response.status_code, 'unknown')



def _patch(django):
    Pin(service=config.django['service_name']).onto(django)
    wrap(django, 'apps.registry.Apps.populate', traced_populate(django))
    wrap(django, 'core.handlers.base.BaseHandler.load_middleware', traced_load_middleware(django))
    wrap(django, 'core.handlers.base.BaseHandler.get_response', traced_get_response(django))


def patch():
    import django
    if getattr(django, '_datadog_patch', False):
        return

    _patch(django)

    setattr(django, '_datadog_patch', True)


def _unpatch(django):
    unwrap(django, 'apps.config.Apps.populate')
    unwrap(django, 'core.handlers.base.BaseHandler.load_middleware')
    unwrap(django, 'core.handlers.base.BaseHandler.get_response')


def unpatch():
    import django
    if not getattr(django, '_datadog_patch', False):
        return

    _unpatch(django)

    setattr(django, '_datadog_patch', False)
