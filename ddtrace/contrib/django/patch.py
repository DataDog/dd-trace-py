"""
The Django patching works as follows:

Django internals are instrumented via normal `patch()`.

django.apps.registry.Apps.populate is patched to add instrumentation for any
specific Django apps like Django Rest Framework (DRF).
"""
import os
import sys

from inspect import isclass, isfunction

from ddtrace import config, Pin
from ddtrace.vendor import wrapt
from wrapt import wrap_function_wrapper as wrap
from ddtrace.utils.wrappers import unwrap, iswrapped

from ..dbapi import TracedCursor as DbApiTracedCursor
from ...compat import parse
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...contrib import func_name
from ...ext import http, sql as sqlx
from ...internal.logger import get_logger
from ...propagation.http import HTTPPropagator


from .compat import get_resolver


log = get_logger(__name__)

config._add('django', dict(
    service_name=os.environ.get('DATADOG_SERVICE_NAME') or 'django',
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


def _set_tag_array(span, prefix, value):
    """Helper to set a span tag as a single value or an array"""
    if not value:
        return

    if len(value) == 1:
        span.set_tag(prefix, value[0])
    else:
        for i, v in enumerate(value, start=0):
            span.set_tag('{0}.{1}'.format(prefix, i), v)


def get_django_2_route(resolver, resolver_match):
    # Try to use `resolver_match.route` if available
    # Otherwise, look for `resolver.pattern.regex.pattern`
    route = resolver_match.route
    if not route:
        # DEV: Use all these `getattr`s to protect against changes between versions
        pattern = getattr(resolver, 'pattern', None)
        if pattern:
            regex = getattr(pattern, 'regex', None)
            if regex:
                route = getattr(regex, 'pattern', '')

    return route


def patch_conn(django, conn):
    def cursor(django, pin, func, instance, args, kwargs):
        database_prefix = ''
        alias = getattr(conn, 'alias', 'default')
        service = '{}{}{}'.format(database_prefix, alias, 'db')
        vendor = getattr(conn, 'vendor', 'db')
        prefix = sqlx.normalize_vendor(vendor)
        tags = {
            'django.db.vendor': vendor,
            'django.db.alias': alias,
        }
        pin = Pin(service, tags=tags, tracer=pin.tracer, app=prefix)
        return DbApiTracedCursor(func(*args, **kwargs), pin)

    if not isinstance(conn.cursor, wrapt.ObjectProxy):
        conn.cursor = wrapt.FunctionWrapper(conn.cursor, with_traced_module(cursor)(django))


def instrument_dbs(django):
    for conn in django.db.connections.all():
        patch_conn(django, conn)


def _resource_from_cache_prefix(resource, cache):
    """
    Combine the resource name with the cache prefix (if any)
    """
    if getattr(cache, 'key_prefix', None):
        name = '{} {}'.format(resource, cache.key_prefix)
    else:
        name = resource

    # enforce lowercase to make the output nicer to read
    return name.lower()


def quantize_key_values(key):
    """
    Used in the Django trace operation method, it ensures that if a dict
    with values is used, we removes the values from the span meta
    attributes. For example::

        >>> quantize_key_values({'key': 'value'})
        # returns ['key']
    """
    if isinstance(key, dict):
        return key.keys()

    return key


@with_traced_module
def traced_cache(django, pin, func, instance, args, kwargs):
    cache_service_name = 'django-cache'  # TODO

    # get the original function method
    with pin.tracer.trace('django.cache', span_type='cache', service=cache_service_name) as span:
        # update the resource name and tag the cache backend
        span.resource = _resource_from_cache_prefix(func_name(func), instance)
        cache_backend = '{}.{}'.format(instance.__module__, instance.__class__.__name__)
        span.set_tag('django.cache.backend', cache_backend)

        if args:
            keys = quantize_key_values(args[0])
            span.set_tag('django.cache.key', keys)

        return func(*args, **kwargs)


def instrument_caches(django):
    cache_backends = set([cache['BACKEND'] for cache in django.conf.settings.CACHES.values()])
    for cache_module in cache_backends:
        for method in ['get', 'set', 'add', 'delete', 'incr', 'decr',
                       'get_many', 'set_many', 'delete_many', ]:
            wrap(cache_module, method, traced_cache(django))


@with_traced_module
def traced_populate(django, pin, func, instance, args, kwargs):
    """django.apps.registry.Apps.populate is the method used to populate all the apps.

    It is used as a hook to install instrumentation for 3rd party apps (like DRF).

    `populate()` works in 3 phases:

        - Phase 1: Initializes the app configs and imports the app modules.
        - Phase 2: Imports models modules for each app.
        - Phase 3: runs ready() of each app config.

    If all 3 phases successfully run then `instance.ready` will be `True`.
    """

    # populate() can be called multiple times, we don't want to instrument more than once
    if instance.ready:
        log.info('Django instrumentation already installed, skipping.')
        return func(*args, **kwargs)

    ret = func(*args, **kwargs)

    if not instance.ready:
        log.warning('populate() failed skipping instrumentation.')
        return ret

    # Instrument Databases
    try:
        instrument_dbs(django)
    except Exception:
        log.exception('Error instrumenting Django database connections')

    # Instrument Caches
    try:
        instrument_caches(django)
    except Exception:
        log.exception('Error instrumenting Django caches')

    # Instrument Django Rest Framework
    try:
        from .restframework import patch_restframework
        patch_restframework(pin.tracer)
    except Exception:
        log.exception('Error patching rest_framework')

    return ret


def traced_func(django, name, resource=None):
    """Returns a function to trace Django functions."""
    def wrapped(django, pin, func, instance, args, kwargs):
        with pin.tracer.trace(name, resource=resource):
            return func(*args, **kwargs)
    return with_traced_module(wrapped)(django)


@with_traced_module
def traced_load_middleware(django, pin, func, instance, args, kwargs):
    """Patches django.core.handlers.base.BaseHandler.load_middleware to instrument all middlewares."""
    settings_middleware = []
    # Gather all the middleware
    if getattr(django.conf.settings, 'MIDDLEWARE', None):
        settings_middleware += django.conf.settings.MIDDLEWARE
    if getattr(django.conf.settings, 'MIDDLEWARE_CLASSES', None):
        settings_middleware += django.conf.settings.MIDDLEWARE_CLASSES

    # Iterate over each middleware provided in settings.py
    # Each middleware can either be a function or a class
    for mw_path in settings_middleware:
        mw = django.utils.module_loading.import_string(mw_path)

        # Instrument function-based middleware
        if isfunction(mw) and not iswrapped(mw):
            split = mw_path.split('.')
            if len(split) < 2:
                continue
            base = '.'.join(split[:-1])
            attr = split[-1]

            # Function-based middleware is a factory which returns a handler function for requests.
            # So instead of tracing the factory, we want to trace its returned value.
            # We wrap the factory to return a traced version of the handler function.
            def wrapped_factory(func, instance, args, kwargs):
                # r is the middleware handler function returned from the factory
                r = func(*args, **kwargs)
                return wrapt.FunctionWrapper(r, traced_func(django, 'django.middleware', resource=mw_path))
            wrap(base, attr, wrapped_factory)

        # Instrument class-based middleware
        elif isclass(mw):
            for hook in ['process_request', 'process_response', 'process_view',
                         'process_exception', 'process_template_response', '__call__']:
                if hasattr(mw, hook) and not iswrapped(mw, hook):
                    wrap(mw, hook, traced_func(django, 'django.middleware', resource=mw_path + '.{0}'.format(hook)))
    return func(*args, **kwargs)


@with_traced_module
def traced_get_response(django, pin, func, instance, args, kwargs):
    """Trace django.core.handlers.base.BaseHandler.get_response() (or other implementations).

    This is the main entry point for requests.

    Django requests are handled by a Handler.get_response method (inherited from base.BaseHandler).
    This method invokes the middleware chain and returns the response generated by the chain.
    """

    request = args[0] if len(args) > 0 else kwargs.get('request', None)
    if request is None:
        return func(*args, **kwargs)

    try:
        # Django 2.2.0 added `request.headers` which is a case sensitive dict of headers
        # For previous versions use `request.META` a dict with `HTTP_X_UPPER_CASE_HEADER` keys
        if django.VERSION >= (2, 2, 0):
            request_headers = request.headers
        else:
            request_headers = request.META

        if config.django['distributed_tracing_enabled']:
            context = propagator.extract(request_headers)
            if context.trace_id:
                pin.tracer.context_provider.activate(context)

        # Determine the resolver and resource name for this request
        if hasattr(request, 'urlconf'):
            urlconf = request.urlconf
            resolver = get_resolver(urlconf)
        else:
            resolver = get_resolver()

        if django.VERSION < (1, 10, 0):
            error_type_404 = django.core.urlresolvers.Resolver404
        else:
            error_type_404 = django.urls.exceptions.Resolver404

        route = None
        resolver_match = None
        resource = request.method
        try:
            # Resolve the requested url
            resolver_match = resolver.resolve(request.path_info)

            # Determine the resource name to use
            # In Django >= 2.2.0 we can access the original route or regex pattern
            if django.VERSION >= (2, 2, 0):
                route = get_django_2_route(resolver, resolver_match)
                if route:
                    resource = '{0} {1}'.format(request.method, route)
                else:
                    resource = request.method
            # Older versions just use the view/handler name, e.g. `views.MyView.handler`
            else:
                # TODO: Validate if `resolver.pattern.regex.pattern` is available or not
                callback, callback_args, callback_kwargs = resolver_match
                resource = '{0} {1}'.format(request.method, func_name(callback))

        except error_type_404:
            # Normalize all 404 requests into a single resource name
            # DEV: This is for potential cardinality issues
            resource = '{0} 404'.format(request.method)
        except Exception as ex:
            log.warning(
                'Failed to resolve request path %r with path info %r, %r',
                request,
                getattr(request, 'path_info', 'not-set'),
                ex,
            )

        with pin.tracer.trace(
                'django.request', resource=resource, service=config.django['service_name'], span_type=http.TYPE
        ) as span:
            analytics_sr = config.django.get_analytics_sample_rate(use_global_config=True)
            if analytics_sr is not None:
                span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, analytics_sr)

            span.set_tag('django.request.class', func_name(request))
            span.set_tag(http.METHOD, request.method)
            if config.django['trace_query_string']:
                span.set_tag(http.QUERY_STRING, request_headers['QUERY_STRING'])

            # Not a 404 request
            if resolver_match:
                span.set_tag('django.view', resolver_match.view_name)
                _set_tag_array(span, 'django.namespace', resolver_match.namespaces)

                # Django >= 2.0.0
                if hasattr(resolver_match, 'app_names'):
                    _set_tag_array(span, 'django.app', resolver_match.app_names)

            if route:
                span.set_tag('http.route', route)

            # Set HTTP Request tags
            # Build `http.url` tag value from request info
            # DEV: We are explicitly omitting query strings since they may contain sensitive information
            span.set_tag(
                http.URL,
                parse.urlunparse(parse.ParseResult(
                    scheme=request.scheme,
                    netloc=request.get_host(),  # this will include `host:port`
                    path=request.path,
                    params='',
                    query='',
                    fragment='',
                ))
            )

            response = func(*args, **kwargs)

            if response:
                span.set_tag(http.STATUS_CODE, response.status_code)
                span.set_tag('django.response.class', func_name(response))
                if hasattr(response, 'template_name'):
                    _set_tag_array(span, 'django.response.template', response.template_name)
            return response
    except Exception:
        log.debug('Failed to trace django request %r', args, exc_info=True)
        return func(*args, **kwargs)


@with_traced_module
def traced_template_render(django, pin, wrapped, instance, args, kwargs):
    """Instrument django.template.base.Template.render for tracing template rendering."""
    template_name = getattr(instance, 'name', None)
    if template_name:
        resource = template_name
    else:
        resource = '{0}.{1}'.format(func_name(instance), wrapped.__name__)

    with pin.tracer.trace('django.template.render', resource=resource) as span:
        if template_name:
            span.set_tag('django.template.name', template_name)
        engine = getattr(instance, 'engine', None)
        if engine:
            span.set_tag('django.template.engine.class', func_name(engine))

        return wrapped(*args, **kwargs)


def instrument_view(django, view):
    """Helper to wrap Django views."""

    # All views should be callable, double check before doing anything
    if not callable(view) or isinstance(view, wrapt.ObjectProxy):
        return view

    # Patch view HTTP methods and lifecycle methods
    http_method_names = getattr(view, 'http_method_names', ('get', 'delete', 'post', 'options', 'head'))
    lifecycle_methods = ('setup', 'dispatch', 'http_method_not_allowed')
    for name in list(http_method_names) + list(lifecycle_methods):
        try:
            func = getattr(view, name, None)
            # Do not wrap if the method does not exist or is already wrapped
            if not func or isinstance(func, wrapt.ObjectProxy):
                continue

            resource = '{0}.{1}'.format(func_name(view), name)
            op_name = 'django.view.{0}'.format(name)
            wrap(view, name, traced_func(django, name=op_name, resource=resource)(func))
        except Exception:
            log.debug('Failed to patch django view %r function %s', view, name, exc_info=True)

    # Patch response methods
    response_cls = getattr(view, 'response_class', None)
    if response_cls:
        methods = ('render', )
        for name in methods:
            try:
                func = getattr(response_cls, name, None)
                # Do not wrap if the method does not exist or is already wrapped
                if not func or isinstance(func, wrapt.ObjectProxy):
                    continue

                resource = '{0}.{1}'.format(func_name(response_cls), name)
                op_name = 'django.response.{0}'.format(name)
                wrap(response_cls, name, traced_func(django, name=op_name, resource=resource)(func))
            except Exception:
                log.debug('Failed to patch django response %r function %s', response_cls, name, exc_info=True)

    # Return a wrapped version of this view
    return wrapt.FunctionWrapper(view, traced_func(django, 'django.view', resource=func_name(view)))


@with_traced_module
def traced_urls_path(django, pin, wrapped, instance, args, kwargs):
    """Wrapper for url path helpers to ensure all views registered as urls are traced."""
    try:
        if 'view' in kwargs:
            kwargs['view'] = instrument_view(django, kwargs['view'])
        elif len(args) >= 2:
            args = list(args)
            args[1] = instrument_view(django, args[1])
            args = tuple(args)
    except Exception:
        log.debug('Failed to wrap django url path %r %r', args, kwargs, exc_info=True)
    return wrapped(*args, **kwargs)


@with_traced_module
def traced_as_view(django, pin, func, instance, args, kwargs):
    """
    Wrapper for django's View.as_view class method
    """
    if len(args) > 1:
        view = args[1]
        instrument_view(django, view)
    return func(*args, **kwargs)


def _patch(django):
    Pin(service=config.django['service_name']).onto(django)
    wrap(django, 'apps.registry.Apps.populate', traced_populate(django))

    # DEV: this check will be replaced with import hooks in the future
    if 'django.core.handlers.base' not in sys.modules:
        import django.core.handlers.base
    wrap(django, 'core.handlers.base.BaseHandler.load_middleware', traced_load_middleware(django))
    wrap(django, 'core.handlers.base.BaseHandler.get_response', traced_get_response(django))

    # DEV: this check will be replaced with import hooks in the future
    if 'django.template.base' not in sys.modules:
        import django.template.base
    wrap(django, 'template.base.Template.render', traced_template_render(django))

    # DEV: this check will be replaced with import hooks in the future
    if 'django.conf.urls.static' not in sys.modules:
        import django.conf.urls.static
    wrap(django, 'conf.urls.static.static', traced_urls_path(django))
    wrap(django, 'conf.urls.url', traced_urls_path(django))
    if django.VERSION >= (2, 0, 0):
        wrap(django, 'urls.path', traced_urls_path(django))
        wrap(django, 'urls.re_path', traced_urls_path(django))

    # DEV: this check will be replaced with import hooks in the future
    if 'django.views.generic.base' not in sys.modules:
        import django.views.generic.base
    wrap(django, 'views.generic.base.View.as_view', traced_as_view(django))


def patch():
    # DEV: this import will eventually be replaced with the module given from an import hook
    import django
    if getattr(django, '_datadog_patch', False):
        return
    _patch(django)

    setattr(django, '_datadog_patch', True)


def _unpatch(django):
    unwrap(django.apps.registry.Apps, 'populate')
    unwrap(django.core.handlers.base.BaseHandler, 'load_middleware')
    unwrap(django.core.handlers.base.BaseHandler, 'get_response')
    unwrap(django.template.base.Template, 'render')
    unwrap(django.conf.urls.static, 'static')
    unwrap(django.conf.urls, 'url')
    if django.VERSION >= (2, 0, 0):
        unwrap(django.urls, 'path')
        unwrap(django.urls, 're_path')
    unwrap(django.views.generic.base.View, 'as_view')


def unpatch():
    import django
    if not getattr(django, '_datadog_patch', False):
        return

    _unpatch(django)

    setattr(django, '_datadog_patch', False)
