import functools
import logging

from ddtrace import config
from ddtrace.vendor import wrapt

from ...utils.wrappers import unwrap
from ...propagation.http import HTTPPropagator
from ...pin import Pin
from ...ext import http as ext_http
from ..httplib.patch import should_skip_request

import aiohttp
from yarl import URL

try:
    # instrument external packages only if they're available
    import aiohttp_jinja2
    from .template import _trace_render_template
except ImportError:
    _trace_render_template = None


log = logging.getLogger(__name__)


def _get_url_obj(obj):
    url_obj = obj.url

    if not isinstance(url_obj, URL):
        url_obj = getattr(obj, 'url_obj', None)  # 1.x

    return url_obj


def _set_request_tags(span, url):
    if (url.scheme == 'http' and url.port == 80) or (url.scheme == 'https' and url.port == 443):
        port = ''
    else:
        port = ':{}'.format(url.port)

    url_str = '{scheme}://{host}{port}{path}'.format(
        scheme=url.scheme, host=url.host, port=port, path=url.path)
    span.set_tag(ext_http.URL, url_str)
    span.resource = url.path


class _WrappedConnectorClass(wrapt.ObjectProxy):
    def __init__(self, obj, pin):
        super().__init__(obj)
        pin.onto(self)

    async def connect(self, req, *args, **kwargs):
        pin = Pin.get_from(self)
        with pin.tracer.trace('{}.connect'.format(self.__class__.__name__),
                              span_type=ext_http.TYPE, service=pin.service) as span:
            _set_request_tags(span, _get_url_obj(req))
            # We call this way so "self" will not get sliced and call
            # _create_connection on us first
            result = await self.__wrapped__.__class__.connect(self, req, *args, **kwargs)
            return result

    async def _create_connection(self, req, *args, **kwargs):
        pin = Pin.get_from(self)
        with pin.tracer.trace(
                '{}._create_connection'.format(self.__class__.__name__),
                span_type=ext_http.TYPE, service=pin.service) as span:
            _set_request_tags(span, _get_url_obj(req))
            result = await self.__wrapped__._create_connection(req, *args, **kwargs)
            return result


class _WrappedResponseClass(wrapt.ObjectProxy):
    def __init__(self, obj, pin, trace_headers):
        super().__init__(obj)

        pin.onto(self)

        # We'll always have a parent span from outer request
        ctx = pin.tracer.get_call_context()
        parent_span = ctx.get_current_span()
        if parent_span:
            self._self_parent_trace_id = parent_span.trace_id
            self._self_parent_span_id = parent_span.span_id
        else:
            self._self_parent_trace_id, self._self_parent_span_id = ctx.trace_id, ctx.span_id

        self._self_trace_headers = trace_headers

    async def start(self, *args, **kwargs):
        # This will get called once per connect
        pin = Pin.get_from(self)

        # This will parent correctly as we'll always have an enclosing span
        with pin.tracer.trace('{}.start'.format(self.__class__.__name__),
                              span_type=ext_http.TYPE, service=pin.service) as span:
            _set_request_tags(span, _get_url_obj(self))

            resp = await self.__wrapped__.start(*args, **kwargs)

            if self._self_trace_headers:
                tags = {hdr: resp.headers[hdr]
                        for hdr in self._self_trace_headers
                        if hdr in resp.headers}
                span.set_tags(tags)

            span.set_tag(ext_http.STATUS_CODE, self.status)
            span.set_tag(ext_http.METHOD, resp.method)

        return resp

    async def read(self, *args, **kwargs):
        pin = Pin.get_from(self)
        # This may not have an immediate parent as the request completed
        with pin.tracer.trace('{}.read'.format(self.__class__.__name__),
                              span_type=ext_http.TYPE, service=pin.service) as span:

            if self._self_parent_trace_id:
                span.trace_id = self._self_parent_trace_id

            if self._self_parent_span_id:
                span.parent_id = self._self_parent_span_id

            _set_request_tags(span, _get_url_obj(self))
            result = await self.__wrapped__.read(*args, **kwargs)
            span.set_tag(ext_http.STATUS_CODE, self.status)
            span.set_tag('Length', len(result))

        return result

    async def __aenter__(self):
        result = await self.__wrapped__.__aenter__()
        return result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        result = await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
        return result


class _WrappedRequestContext(wrapt.ObjectProxy):
    def __init__(self, obj, pin, span, trace_headers, trace_context):
        super().__init__(obj)
        pin.onto(self)
        self._self_span = span
        self._self_trace_headers = trace_headers
        self._self_trace_context = trace_context
        self._self_have_context = False

    async def _handle_response(self, coro):
        try:
            resp = await coro

            if self._self_trace_headers:
                tags = {hdr: resp.headers[hdr]
                        for hdr in self._self_trace_headers
                        if hdr in resp.headers}
                self._self_span.set_tags(tags)

            self._self_span.set_tag(ext_http.STATUS_CODE, resp.status)
            self._self_span.error = int(500 <= resp.status)
            return resp
        except BaseException:
            self._self_span.set_traceback()
            raise
        finally:
            if not self._self_have_context or not self._self_trace_context:
                self._self_span.finish()

    # this will get when called without a context
    def __iter__(self):
        return self.__await__()

    def __await__(self):
        resp = self._handle_response(self.__wrapped__).__await__()
        return resp

    async def __aenter__(self):
        self._self_have_context = True
        resp = await self._handle_response(self.__wrapped__.__aenter__())
        return resp

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            resp = await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if self._self_have_context and self._self_trace_context:
                self._self_span.finish()
        return resp


def _create_wrapped_request(method, enable_distributed, trace_headers,
                            trace_context, func, instance, args, kwargs):
    pin = Pin.get_from(instance)

    if not pin.tracer.enabled:
        return func(*args, **kwargs)

    if method == 'REQUEST':
        method = kwargs.get("method", args[0])
        url = URL(kwargs.get("url", args[1]))
    else:
        url = URL(kwargs.get("url", args[0]))

    if should_skip_request(pin, url):
        result = func(*args, **kwargs)
        return result

    service = pin.service

    # Create a new span and attach to this instance (so we can
    # retrieve/update/close later on the response)
    # Note that we aren't tracing redirects
    span = pin.tracer.trace('ClientSession.request', span_type=ext_http.TYPE, service=service)

    if enable_distributed:
        headers = kwargs.get('headers', {})
        if headers is None:
            headers = {}
        propagator = HTTPPropagator()
        propagator.inject(span.context, headers)
        kwargs['headers'] = headers

    _set_request_tags(span, url)
    span.set_tag(ext_http.METHOD, method)

    obj = _WrappedRequestContext(func(*args, **kwargs), pin, span, trace_headers, trace_context)
    return obj


def _create_wrapped_response(client_session, trace_headers, cls, instance, args, kwargs):
    obj = _WrappedResponseClass(cls(*args, **kwargs), Pin.get_from(client_session), trace_headers)
    return obj


def _wrap_clientsession_init(trace_headers, func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)

    # bail on the tracing if not enabled.
    if not pin.tracer.enabled:
        return func(*args, **kwargs)

    response_class = kwargs.get('response_class', aiohttp.ClientResponse)

    connector = kwargs.get('connector')
    if not connector:
        connector = aiohttp.TCPConnector()

    kwargs['connector'] = _WrappedConnectorClass(connector, pin)

    wrapper = functools.partial(_create_wrapped_response, instance, trace_headers)
    kwargs['response_class'] = wrapt.FunctionWrapper(response_class, wrapper)

    return func(*args, **kwargs)


_clientsession_wrap_methods = {
    'get', 'options', 'head', 'post', 'put', 'patch', 'delete', 'request'
}


def patch(tracer=None, enable_distributed=False, trace_headers=None, trace_context=False):
    """
    Patch aiohttp third party modules:
        * aiohttp_jinja2
        * aiohttp ClientSession request

    :param trace_context: set to true to expand span to life of response
                          context
    :param trace_headers: set of headers to trace
    :param tracer: tracer to use
    :param enable_distributed: enable aiohttp client to set parent span IDs in
                               requests
    """

    _w = wrapt.wrap_function_wrapper
    if not getattr(aiohttp, '__datadog_patch', False):
        setattr(aiohttp, '__datadog_patch', True)
        pin = Pin(service='aiohttp.client', app='aiohttp', tracer=tracer)
        pin.onto(aiohttp.ClientSession)

        wrapper = functools.partial(_wrap_clientsession_init, trace_headers)
        _w('aiohttp', 'ClientSession.__init__', wrapper)

        for method in _clientsession_wrap_methods:
            wrapper = functools.partial(_create_wrapped_request,
                                        method.upper(), enable_distributed,
                                        trace_headers, trace_context)
            _w('aiohttp', 'ClientSession.{}'.format(method), wrapper)

    if _trace_render_template and \
            not getattr(aiohttp_jinja2, '__datadog_patch', False):
        setattr(aiohttp_jinja2, '__datadog_patch', True)

        _w('aiohttp_jinja2', 'render_template', _trace_render_template)
        Pin(app='aiohttp', service=config.service, tracer=tracer).onto(aiohttp_jinja2)


def unpatch():
    """
    Remove tracing from patched modules.
    """
    if getattr(aiohttp, '__datadog_patch', False):
        unwrap(aiohttp.ClientSession, '__init__')

        for method in _clientsession_wrap_methods:
            unwrap(aiohttp.ClientSession, method)

        setattr(aiohttp, '__datadog_patch', False)

    if _trace_render_template and getattr(aiohttp_jinja2, '__datadog_patch', False):
        setattr(aiohttp_jinja2, '__datadog_patch', False)
        unwrap(aiohttp_jinja2, 'render_template')
