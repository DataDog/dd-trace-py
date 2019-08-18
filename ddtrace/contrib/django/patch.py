import os

import django
import django.conf.urls
import django.conf.urls.static
import django.core.handlers.base
import django.template.base
import django.views.generic.base

from ... import config
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...compat import parse
from ...ext import AppTypes, http
from ...internal.logger import get_logger
# from ...http import store_request_headers, store_response_headers
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ...utils.importlib import func_name
from ...utils.wrappers import unwrap as _u
from ...vendor import wrapt
from ...vendor.wrapt import wrap_function_wrapper as _w

from .compat import get_resolver

log = get_logger(__name__)

config._add('django', dict(
    service_name=os.environ.get('DATADOG_SERVICE_NAME') or 'django',
    app='django',
    app_type=AppTypes.web,

    distributed_tracing_enabled=True,
))


def patch():
    """Patch the instrumented methods
    """
    global django
    if getattr(django, '_datadog_patch', False):
        return
    setattr(django, '_datadog_patch', True)

    # Wrap main request entrypoint
    _w(django.core.handlers.base, 'BaseHandler.get_response', wrap_get_response)

    # Wrap function used to wrap all middleware
    if django.VERSION >= (2, 0, 0):
        import django.core.handlers.exception
        _w(django.core.handlers.base, 'convert_exception_to_response', wrap_convert_exception_to_response)
        _w(django.core.handlers.exception, 'convert_exception_to_response', wrap_convert_exception_to_response)
    else:
        _w(django.core.handlers.base, 'BaseHandler.load_middleware', wrap_load_middleware)

    # Wrap template rendering
    _w(django.template.base, 'Template.render', wrap_template_render)

    # Wrap URL helpers
    _w(django.conf.urls.static, 'static', wrap_urls_path)
    _w(django.conf.urls, 'url', wrap_urls_path)
    if django.VERSION >= (2, 0, 0):
        import django.urls
        _w(django.urls, 'path', wrap_urls_path)
        _w(django.urls, 're_path', wrap_urls_path)

    # Wrap view methods
    _w(django.views.generic.base, 'View.as_view', wrap_as_view)

    # Setup pin on `django` module
    Pin(
        service=config.django['service_name'],
        app=config.django['app'],
        app_type=config.django['app_type'],
    ).onto(django)


def unpatch():
    """Unpatch the instrumented methods
    """
    global django
    if not getattr(django, '_datadog_patch', False):
        return
    setattr(django, '_datadog_patch', False)

    _u(django.core.handlers.base.BaseHandler, 'get_response')

    if django.VERSION >= (2, 0, 0):
        _u(django.core.handlers.base.BaseHandler, 'convert_exception_to_response')
        _u(django.core.handlers.exception, 'convert_exception_to_response')
    else:
        _u(django.core.handlers.base.BaseHandler, 'load_middleware')

    _u(django.template.base.Template, 'render')

    if django.VERSION >= (2, 0, 0):
        _u(django.urls, 'path')
        _u(django.urls, 're_path')
    _u(django.conf.urls, 'url')
    _u(django.conf.urls.static, 'static')
    _u(django.views.generic.base.View, 'as_view')


def wrap_load_middleware(wrapped, instance, args, kwargs):
    """
    Wrapper for Django 1.x BaseHandler.load_middleware
    """
    try:
        # Generate middleware first
        return wrapped(*args, **kwargs)
    finally:
        # Middleware holding properties
        middleware_props = (
            '_request_middleware',
            '_view_middleware',
            '_template_response_middleware',
            '_response_middleware',
            '_exception_middleware',
        )
        for prop in middleware_props:
            middleware = getattr(instance, prop, None)
            # Continue if no middleware found or not a list
            if not middleware or not isinstance(middleware, list):
                continue

            middleware = [
                decorate_func('django.middleware')(func)
                for func in middleware
                if callable(func) and not isinstance(func, wrapt.ObjectProxy)
            ]
            setattr(instance, prop, middleware)


def wrap_urls_path(wrapped, instance, args, kwargs):
    """
    Wrapper for url path helpers to ensure all views registered as urls are traced
    """
    try:
        if 'view' in kwargs:
            kwargs['view'] = wrap_view(kwargs['view'])
        elif len(args) >= 2:
            args = list(args)
            args[1] = wrap_view(args[1])
            args = tuple(args)
    except Exception:
        log.debug('Failed to wrap django url path %r %r', args, kwargs, exc_info=True)
    return wrapped(*args, **kwargs)


def wrap_template_render(wrapped, instance, args, kwargs):
    """
    Wrapper for base Template.render method
    """
    pin = Pin.get_from(django)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

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


def decorate_func(name, resource=None):
    """
    Helper used to wrap a function for django tracing
    """
    @wrapt.decorator
    def wrapper(wrapped, instance, args, kwargs):
        pin = Pin.get_from(django)
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)

        with pin.tracer.trace(name, resource=resource or func_name(wrapped)):
            return wrapped(*args, **kwargs)
    return wrapper


def wrap_as_view(wrapped, instance, args, kwargs):
    """
    Wrapper for django's View.as_view class method
    """
    wrap_view(instance)
    return wrapped(*args, **kwargs)


def wrap_view(view):
    """
    Helper to wrap a Django views
    """
    # All views should be callable, double check before doing anything
    if not callable(view):
        return view

    # Ensure the view is not already wrapped
    if isinstance(view, wrapt.ObjectProxy):
        return view

    # Patch View HTTP methods and lifecycle methods
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
            setattr(view, name, decorate_func(name=op_name, resource=resource)(func))
        except Exception:
            log.debug('Failed to patch django view %r function %s', view, name, exc_info=True)

    # Patch Response methods
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
                setattr(response_cls, name, decorate_func(name=op_name, resource=resource)(func))
            except Exception:
                log.debug('Failed to patch django response %r function %s', response_cls, name, exc_info=True)

    # Return a wrapped version of this view
    return decorate_func('django.view')(view)


def wrap_convert_exception_to_response(wrapped, instance, args, kwargs):
    """
    Wrapper used to intercept and wrap all middleware
    """
    get_response = args[0] if len(args) else kwargs.get('get_response')
    if not get_response:
        return wrapped(*args, **kwargs)

    # Do not double wrap
    if isinstance(get_response, wrapt.ObjectProxy):
        return wrapped(*args, **kwargs)

    try:
        # Wrap the middleware class
        # TODO: Should we treat `BaseHandler._get_response` differently?
        get_response = decorate_func('django.middleware')(get_response)

        # Wrap methods of the middleware class
        for attr in ('process_view', 'process_template_response', 'process_exception'):
            func = getattr(get_response, attr, None)
            # Do not wrap if the method does not exist or is already wrapped
            if not func or isinstance(func, wrapt.ObjectProxy):
                continue

            func = decorate_func('django.middleware.{}'.format(attr))(func)
            setattr(get_response, attr, func)

        return wrapped(get_response, *args[1:], **kwargs)
    except Exception:
        log.debug('Failed to wrap django middleware %r', get_response, exc_info=True)
        return wrapped(*args, **kwargs)


def wrap_get_response(wrapped, instance, args, kwargs):
    """
    Wrap the main entrypoint for handling a django web request
    """
    # Ensure the pin exists and is enabled
    pin = Pin.get_from(django)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    # Ensure we have a request
    request = args[0] if len(args) else kwargs.get('request')
    if not request:
        return wrapped(*args, **kwargs)

    try:
        # Django 2.2.0 added `request.headers` which is a case sensitive dict of headers
        #   For previous versions use `request.META` a dict with `HTTP_X_UPPER_CASE_HEADER` keys
        if django.VERSION >= (2, 2, 0):
            request_headers = request.headers
        else:
            request_headers = request.META

        # Configure distributed tracing
        if config.django.get('distributed_tracing_enabled', False):
            propagator = HTTPPropagator()
            # DEV: HTTPPropagator knows how to handle `request.headers` and `request.META` keys
            context = propagator.extract(request_headers)
            # Only need to activate the new context if something was propagated
            if context.trace_id:
                pin.tracer.context_provider.activate(context)

        # Determine the resolver and resource name for this request
        if hasattr(request, 'urlconf'):
            urlconf = request.urlconf
            resolver = get_resolver(urlconf)
        else:
            resolver = get_resolver()

        error_type_404 = None
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

        # Start the `django.request` span
        with pin.tracer.trace(
                name='django.request', service=pin.service, resource=resource, span_type=http.TYPE,
        ) as span:
            # set analytics sample rate with global config enabled
            rate = config.django.get_analytics_sample_rate(use_global_config=True)
            if rate is not None:
                span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, rate)

            # Set Django specific tags
            span.set_tag('django.request.class', func_name(request))

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
            span.set_tag(http.METHOD, request.method)

            # Attach any request headers
            # TODO: How do we do this when we have `request.META`?
            # store_request_headers(request_headers, span, config.django)

            # Fetch the response
            response = wrapped(*args, **kwargs)

            # Set response tags
            span.set_tag(http.STATUS_CODE, response.status_code)
            span.set_tag('django.response.class', func_name(response))
            if hasattr(response, 'template_name'):
                _set_tag_array(span, 'django.response.template', response.template_name)

            # Attach any response headers
            # TODO: How do we get headers from the response?
            # store_response_headers(response, span, config.django)

            return response
    except Exception:
        log.debug('failed to trace django request, %r', args, exc_info=True)
        return wrapped(*args, **kwargs)


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
        # DEV: USe all these `getattr`s to protect against changes between versions
        pattern = getattr(resolver, 'pattern', None)
        if pattern:
            regex = getattr(pattern, 'regex', None)
            if regex:
                route = getattr(regex, 'pattern', '')

    return route
