import asyncio
import functools
import logging
import wrapt

from ddtrace.util import unwrap

from .middlewares import _SPAN_MIN_ERROR, PARENT_TRACE_HEADER_ID, \
    PARENT_SPAN_HEADER_ID
from ...pin import Pin
from ...ext import http as ext_http
from ..httplib.patch import should_skip_request
import aiohttp.client
from aiohttp.client import URL

try:
    # instrument external packages only if they're available
    import aiohttp_jinja2
    from .template import _trace_render_template
except ImportError:
    _trace_render_template = None


log = logging.getLogger(__name__)


class _WrappedResponseClass(wrapt.ObjectProxy):
    @asyncio.coroutine
    def start(self, *args, **kwargs):
        # This will get called once per connect
        pin = Pin.get_from(self)

        # This will parent correctly as we'll always have an enclosing span
        with pin.tracer.trace('{}.start'.format(self.__class__.__name__),
                              span_type=ext_http.TYPE) as span:
            _set_request_tags(span, getattr(self, 'url_obj', self.url))
            result = yield from self.__wrapped__.start(*args, **kwargs)  # noqa: E999
            span.set_tag(ext_http.STATUS_CODE, self.status)
            span.error = int(_SPAN_MIN_ERROR <= self.status)

        return result

    @asyncio.coroutine
    def read(self, *args, **kwargs):
        pin = Pin.get_from(self)
        # This will not have a parent as the request completed
        parent_span = getattr(self, '_datadog_span')
        with pin.tracer.trace('{}.read'.format(self.__class__.__name__),
                              span_type=ext_http.TYPE) as span:
            span.trace_id = parent_span.trace_id
            span.parent_id = parent_span.span_id
            _set_request_tags(span, getattr(self, 'url_obj', self.url))
            result = yield from self.__wrapped__.read(*args, **kwargs)  # noqa: E999
            span.set_tag(ext_http.STATUS_CODE, self.status)
            span.error = int(_SPAN_MIN_ERROR <= self.status)

        return result


def _create_wrapped_response(client_session, cls, instance, args, kwargs):
    obj = _WrappedResponseClass(cls(*args, **kwargs))
    Pin.get_from(client_session).onto(obj)
    span = getattr(client_session, '_datadog_span')
    setattr(obj, '_datadog_span', span)
    return obj


def _wrap_clientsession_init(func, instance, args, kwargs):
    response_class = kwargs.get('response_class', aiohttp.client.ClientResponse)
    wrapper = functools.partial(_create_wrapped_response, instance)
    kwargs['response_class'] = wrapt.FunctionWrapper(response_class, wrapper)

    return func(*args, **kwargs)


def _set_request_tags(span, url):
    if (url.scheme == 'http' and url.port == 80) or \
            (url.scheme == 'https' and url.port == 443):
        port = ''
    else:
        port = url.port

    url_str = '{scheme}://{host}{port}{path}'.format(
        scheme=url.scheme, host=url.host, port=port, path=url.path)
    span.set_tag(ext_http.URL, url_str)
    span.resource = url.path


@asyncio.coroutine
def _wrap_request(enable_distributed, func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)
    method, url = args[0], URL(args[1])

    if should_skip_request(pin, url):
        result = yield from func(*args, **kwargs)  # noqa: E999
        return result

    # Create a new span and attach to this instance (so we can
    # retrieve/update/close later on the response)
    # Note that we aren't tracing redirects
    with pin.tracer.trace('ClientSession.request',
                          span_type=ext_http.TYPE) as span:
        setattr(instance, '_datadog_span', span)

        if enable_distributed:
            headers = kwargs.get('headers', {})
            headers[PARENT_TRACE_HEADER_ID] = str(span.trace_id)
            headers[PARENT_SPAN_HEADER_ID] = str(span.span_id)
            kwargs['headers'] = headers

        _set_request_tags(span, url)
        span.set_tag(ext_http.METHOD, method)

        resp = yield from func(*args, **kwargs)  # noqa: E999

        span.set_tag(ext_http.STATUS_CODE, resp.status)
        span.error = int(_SPAN_MIN_ERROR <= resp.status)

        return resp


def patch(tracer=None, enable_distributed=False):
    """
    Patch aiohttp third party modules:
        * aiohttp_jinja2
        * aiohttp ClientSession request

    :param tracer: tracer to use
    :param enable_distributed: enable aiohttp client to set parent span IDs in
                               requests
    """

    _w = wrapt.wrap_function_wrapper
    if not getattr(aiohttp, '__datadog_patch', False):
        setattr(aiohttp, '__datadog_patch', True)
        pin = Pin(app='aiohttp', service=None, app_type=ext_http.TYPE,
                  tracer=tracer)
        pin.onto(aiohttp.client.ClientSession)

        _w('aiohttp.client', 'ClientSession.__init__', _wrap_clientsession_init)

        wrapper = functools.partial(_wrap_request, enable_distributed)
        _w('aiohttp.client', 'ClientSession._request', wrapper)

    if _trace_render_template and \
            not getattr(aiohttp_jinja2, '__datadog_patch', False):
        setattr(aiohttp_jinja2, '__datadog_patch', True)

        _w('aiohttp_jinja2', 'render_template', _trace_render_template)
        Pin(app='aiohttp', service=None, app_type='web',
            tracer=tracer).onto(aiohttp_jinja2)


def unpatch():
    """
    Remove tracing from patched modules.
    """
    if getattr(aiohttp, '__datadog_patch', False):
        unwrap(aiohttp.client.ClientSession, '__init__')
        unwrap(aiohttp.client.ClientSession, '_request')

    if _trace_render_template and getattr(aiohttp_jinja2, '__datadog_patch',
                                          False):
        setattr(aiohttp_jinja2, '__datadog_patch', False)
        unwrap(aiohttp_jinja2, 'render_template')
