import os

import django
import django.core.handlers.base

from ... import config
from ...constants import ANALYTICS_SAMPLE_RATE_KEY
from ...ext import AppTypes, http
from ...internal.logger import get_logger
from ...http import store_request_headers, store_response_headers
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ...utils.importlib import func_name
from ...utils.wrappers import unwrap as _u
from ...vendor.wrapt import wrap_function_wrapper as _w

from .compat import get_resolver

log = get_logger(__name__)

config._add('django', dict(
    # DEV: Environment variable 'DATADOG_SERVICE_NAME' used for backwards compatibility
    service_name=os.environ.get('DATADOG_SERVICE_NAME') or 'django',
    app='flask',
    app_type=AppTypes.web,

    distributed_tracing_enabled=True,
))


def patch():
    """Patch the instrumented methods
    """
    if getattr(django, '_datadog_patch', False):
        return
    setattr(django, '_datadog_patch', True)

    _w(django.core.handlers.base, 'BaseHandler.get_response', wrap_get_response)

    Pin(
        service=config.django['service_name'],
        app=config.django['app'],
        app_type=config.django['app_type'],
    ).onto(django)


def unpatch():
    """Unpatch the instrumented methods
    """
    if not getattr(django, '_datadog_patch', False):
        return
    setattr(django, '_datadog_patch', False)

    _u(django.core.handlers.base.BaseHandler.get_response)


def wrap_get_response(wrapped, instance, args, kwargs):
    pin = Pin.get_from(django)
    if not pin or not pin.enabled():
        return wrapped(*args, **kwargs)

    request = args[0] if len(args) else None
    if not request:
        return wrapped(*args, **kwargs)

    try:
        if hasattr(request, 'urlconf'):
            urlconf = request.urlconf
            resolver = get_resolver(urlconf)
        else:
            resolver = get_resolver()

        resolver_match = resolver.resolve(request.path_info)
        callback, callback_args, callback_kwargs = resolver_match


        # Django 2.2.0 added `request.headers` which is a case sensitive dict of headers
        #   For previous versions use `request.META` a dict with `HTTP_X_UPPER_CASE_HEADER` keys
        if django.VERSION >= (2, 2, 0):
            request_headers = request.headers
        else:
            request_headers = request.META

        # Configure distributed tracing
        if config.django.get('distributed_tracing_enabled', False):
            propagator = HTTPPropagator()
            context = propagator.extract(request_headers)
            # Only need to activate the new context if something was propagated
            if context.trace_id:
                pin.tracer.context_provider.activate(context)

        # Determine the resource name to use
        # In Django >= 2.2.0 we have access to the original route regex
        if django.VERSION >= (2, 2, 0):
            resource = '{0} {1}'.format(request.method, resolver_match.route)

        # Older versions just use the view/handler name, e.g. `views.MyView.handler`
        else:
            resource = '{0} {1}'.format(request.method, func_name(callback))

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
            span.set_tag('django.view', resolver_match.view_name)
            span.set_tag('django.url', resolver_match.url_name)
            _set_tag_array(span, 'django.namespace', resolver_match.namespaces)
            _set_tag_array(span, 'django.app', resolver_match.app_names)

            # Set HTTP Request tags
            span.set_tag(http.URL, request.build_absolute_uri())
            span.set_tag(http.METHOD, request.method)
            if django.VERSION >= (2, 2, 0):
                span.set_tag('http.route', resolver_match.route)

            # Attach any request headers
            # TODO: How do we do this when we have `request.META`?
            # store_request_headers(request_headers, span, config.django)

            # Fetch the response
            response = wrapped(*args, **kwargs)

            # Set response tags
            span.set_tag(http.STATUS_CODE, response.status_code)
            span.set_tag('django.response.class', func_name(response))
            for template, i in enumerate(response.template_name, start=0):
                span.set_tag('django.response.template.{0}'.format(i), template)

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
        for v, i in enumerate(value, start=0):
            span.set_tag('{0}.{1}'.format(prefix, i), v)
