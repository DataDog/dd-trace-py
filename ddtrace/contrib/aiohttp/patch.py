import asyncio
import functools
import logging
import sys
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
PY_35 = sys.version_info >= (3, 5)


# NOTE: this will create a trace for the outer request, and a span for each
#       connect (redirect), and optionally a span for the read of the body


def _get_url_obj(obj):
    url_obj = getattr(obj, 'url_obj', None)  # 1.x
    if url_obj is None:
        url_obj = obj.url  # 2.x

    return url_obj


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


class _WrappedResponseClass(wrapt.ObjectProxy):
    def __init__(self, obj, pin):
        super().__init__(obj)

        pin.onto(self)

        # We'll always have a parent span from outer request
        self._self_parent_span = pin.tracer.current_span()

    @asyncio.coroutine
    def start(self, *args, **kwargs):
        # This will get called once per connect
        pin = Pin.get_from(self)

        # This will parent correctly as we'll always have an enclosing span
        with pin.tracer.trace('{}.start'.format(self.__class__.__name__),
                              span_type=ext_http.TYPE) as span:
            _set_request_tags(span, _get_url_obj(self))
            result = yield from self.__wrapped__.start(*args, **kwargs)  # noqa: E999
            span.set_tag(ext_http.STATUS_CODE, self.status)
            span.error = int(_SPAN_MIN_ERROR <= self.status)

        return result

    @asyncio.coroutine
    def read(self, *args, **kwargs):
        pin = Pin.get_from(self)
        # This may not have an immediate parent as the request completed
        with pin.tracer.trace('{}.read'.format(self.__class__.__name__),
                              span_type=ext_http.TYPE) as span:

            span.trace_id = self._self_parent_span.trace_id
            span.parent_id = self._self_parent_span.span_id

            _set_request_tags(span, _get_url_obj(self))
            result = yield from self.__wrapped__.read(*args, **kwargs)  # noqa: E999
            span.set_tag(ext_http.STATUS_CODE, self.status)
            span.error = int(_SPAN_MIN_ERROR <= self.status)

        return result


class _WrappedRequestContext(wrapt.ObjectProxy):
    def __init__(self, obj, pin, span, trace_headers, trace_context):
        super().__init__(obj)
        pin.onto(self)
        self._self_span = span
        self._self_trace_headers = trace_headers
        self._self_trace_context = trace_context
        self._self_have_context = False

    @asyncio.coroutine
    def _handle_response(self, coro):
        try:
            resp = yield from coro  # noqa: E999

            if self._self_trace_headers:
                tags = {hdr: resp.headers[hdr] for hdr in self._self_trace_headers if hdr in resp.headers}
                self._self_span.set_tags(tags)

            self._self_span.set_tag(ext_http.STATUS_CODE, resp.status)
            self._self_span.error = int(_SPAN_MIN_ERROR <= resp.status)
            return resp
        except:
            self._self_span.set_traceback()
            raise
        finally:
            if not self._self_have_context or not self._self_trace_context:
                self._self_span.finish()

    # this will get when called without a context
    @asyncio.coroutine
    def __iter__(self):
        resp = yield from self._handle_response(self.__wrapped__.__iter__())  # noqa: E999
        return resp

    if PY_35:
        def __await__(self):
            resp = yield from self._handle_response(  # noqa: E999
                self.__wrapped__.__await__())
            return resp

        @asyncio.coroutine
        def __aenter__(self):
            self._self_have_context = True
            resp = yield from self._handle_response(  # noqa: E999
                self.__wrapped__.__aenter__())
            return resp

        @asyncio.coroutine
        def __aexit__(self, exc_type, exc_val, exc_tb):
            try:
                resp = yield from self.__wrapped__.__aexit__(exc_type, exc_val,  # noqa: E999
                                                             exc_tb)
            finally:
               if self._self_have_context and self._self_trace_context:
                    self._self_span.finish()
            return resp


def _create_wrapped_request(method, enable_distributed, trace_headers, trace_context,
                            func, instance, args, kwargs):

    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)

    if 'method' == 'request':
        if 'method' in kwargs:
            method = kwargs['method']
        else:
            method = args[0]

        if 'url' in kwargs:
            url = URL(kwargs['url'])
        else:
            url = args[1]
    else:
        if 'url' in kwargs:
            url = URL(kwargs['url'])
        else:
            url = URL(args[0])

    if should_skip_request(pin, url):
        result = func(*args, **kwargs)
        return result

    # Create a new span and attach to this instance (so we can
    # retrieve/update/close later on the response)
    # Note that we aren't tracing redirects
    span = pin.tracer.trace('ClientSession.request', span_type=ext_http.TYPE)

    _set_request_tags(span, url)
    span.set_tag(ext_http.METHOD, method)

    if enable_distributed:
        headers = kwargs.get('headers', {})
        headers[PARENT_TRACE_HEADER_ID] = str(span.trace_id)
        headers[PARENT_SPAN_HEADER_ID] = str(span.span_id)
        kwargs['headers'] = headers

    obj = _WrappedRequestContext(
        func(*args, **kwargs), pin, span, trace_headers, trace_context)
    return obj


def _create_wrapped_response(client_session, cls, instance, args, kwargs):
    obj = _WrappedResponseClass(cls(*args, **kwargs), Pin.get_from(client_session))
    return obj


def _wrap_clientsession_init(func, instance, args, kwargs):
    response_class = kwargs.get('response_class', aiohttp.client.ClientResponse)
    wrapper = functools.partial(_create_wrapped_response, instance)
    kwargs['response_class'] = wrapt.FunctionWrapper(response_class, wrapper)

    return func(*args, **kwargs)


def patch(tracer=None, enable_distributed=False, trace_headers=None,
          trace_context=False):
    """
    Patch aiohttp third party modules:
        * aiohttp_jinja2
        * aiohttp ClientSession request

    :param trace_context: set to true to expand span to life of response context
    :param trace_headers: set of headers to trace
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

        for method in \
                {'get', 'options', 'head', 'post', 'put', 'patch', 'delete',
                 'request'}:
            wrapper = functools.partial(_create_wrapped_request,
                                        method.upper(), enable_distributed,
                                        trace_headers, trace_context)
            _w('aiohttp.client', 'ClientSession.{}'.format(method), wrapper)

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
