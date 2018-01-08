# Standard library
import logging

# Third party
import wrapt

# Project
from ...compat import httplib, PY2
from ...ext import http as ext_http
from ...pin import Pin
from ...util import unwrap as _u


span_name = 'httplib.request' if PY2 else 'http.client.request'

log = logging.getLogger(__name__)


def _wrap_init(func, instance, args, kwargs):
    Pin(app='httplib', service=None, app_type=ext_http.TYPE).onto(instance)
    return func(*args, **kwargs)


def _wrap_getresponse(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return func(*args, **kwargs)

    resp = None
    try:
        resp = func(*args, **kwargs)
        return resp
    finally:
        try:
            # Get the span attached to this instance, if available
            span = getattr(instance, '_datadog_span', None)
            if span:
                if resp:
                    span.set_tag(ext_http.STATUS_CODE, resp.status)
                    span.error = int(500 <= resp.status)

                span.finish()
                delattr(instance, '_datadog_span')
        except Exception:
            log.debug('error applying request tags', exc_info=True)


def _wrap_putrequest(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)
    if should_skip_request(pin, instance):
        return func(*args, **kwargs)

    try:
        # Create a new span and attach to this instance (so we can retrieve/update/close later on the response)
        span = pin.tracer.trace(span_name, span_type=ext_http.TYPE)
        setattr(instance, '_datadog_span', span)

        method, path = args[:2]
        scheme = 'https' if isinstance(instance, httplib.HTTPSConnection) else 'http'
        port = ':{port}'.format(port=instance.port)
        if (scheme == 'http' and instance.port == 80) or (scheme == 'https' and instance.port == 443):
            port = ''
        url = '{scheme}://{host}{port}{path}'.format(scheme=scheme, host=instance.host, port=port, path=path)
        span.set_tag(ext_http.URL, url)
        span.set_tag(ext_http.METHOD, method)
    except Exception:
        log.debug('error applying request tags', exc_info=True)

    return func(*args, **kwargs)


def should_skip_request(pin, request):
    """Helper to determine if the provided request should be traced"""
    if not pin or not pin.enabled():
        return True

    api = pin.tracer.writer.api
    return request.host == api.hostname and request.port == api.port


def patch():
    """ patch the built-in urllib/httplib/httplib.client methods for tracing"""
    if getattr(httplib, '__datadog_patch', False):
        return
    setattr(httplib, '__datadog_patch', True)

    # Patch the desired methods
    setattr(httplib.HTTPConnection, '__init__',
            wrapt.FunctionWrapper(httplib.HTTPConnection.__init__, _wrap_init))
    setattr(httplib.HTTPConnection, 'getresponse',
            wrapt.FunctionWrapper(httplib.HTTPConnection.getresponse, _wrap_getresponse))
    setattr(httplib.HTTPConnection, 'putrequest',
            wrapt.FunctionWrapper(httplib.HTTPConnection.putrequest, _wrap_putrequest))


def unpatch():
    """ unpatch any previously patched modules """
    if not getattr(httplib, '__datadog_patch', False):
        return
    setattr(httplib, '__datadog_patch', False)

    _u(httplib.HTTPConnection, '__init__')
    _u(httplib.HTTPConnection, 'getresponse')
    _u(httplib.HTTPConnection, 'putrequest')
