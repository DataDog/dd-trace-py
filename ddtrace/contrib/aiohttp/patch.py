import functools
import logging

from ddtrace import config
from ddtrace.vendor import wrapt

from ...utils.wrappers import unwrap, get_root_wrapped
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

propagator = HTTPPropagator()


config._add("aiohttp_client", dict(
    service="aiohttp.client",
    distributed_tracing_enabled=False,
    trace_headers=[],
    trace_context=False,
    trace_query_string=False,
    redact_query_keys=set(),
))

config._add("aiohttp_jinja", dict(
    service="aiohttp"
))


def _get_url_obj(obj):
    url_obj = obj.url

    if not isinstance(url_obj, URL):
        url_obj = getattr(obj, 'url_obj', None)  # 1.x

    return url_obj


def _redacted_query_value(key: str, value: str):
    if key not in config.aiohttp_client.redact_query_keys:
        return value

    return '--redacted--'


def _set_request_tags(span, url: URL, params=None):
    if config.aiohttp_client['trace_query_string']:
        if params:
            url = url.with_query(**{**url.query, **params})

        if url.query and config.aiohttp_client.redact_query_keys:
            url = url.with_query({k: _redacted_query_value(k, v) for k, v in url.query.items()})

        span.set_tag(ext_http.QUERY_STRING, url.query_string)

    sanitized_url = url.with_query(dict())
    span.set_tag(ext_http.URL, str(sanitized_url))
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


class _WrappedStreamReader(wrapt.ObjectProxy):
    def __init__(self, obj, pin, parent_trace_id, parent_span_id, parent_resource):
        super().__init__(obj)

        pin.onto(self)

        self._self_parent_trace_id, self._self_parent_span_id = parent_trace_id, parent_span_id
        self._self_parent_resource = parent_resource

    # These are needed so the correct "self" is associated with the calls
    def __aiter__(self):
        return self.__wrapped__.__class__.__aiter__(self)

    def iter_chunked(self, *args, **kwargs):
        return self.__wrapped__.__class__.iter_chunked(self, *args, **kwargs)

    def iter_any(self, *args, **kwargs):
        return self.__wrapped__.__class__.iter_any(self, *args, **kwargs)

    def iter_chunks(self, *args, **kwargs):
        return self.__wrapped__.__class__.iter_chunks(self, *args, **kwargs)

    # trace read related methods
    async def read(self, *args, **kwargs):
        return await self._read('read', *args, **kwargs)

    async def readline(self, *args, **kwargs):
        return await self._read('readline', *args, **kwargs)

    async def readany(self, *args, **kwargs):
        return await self._read('readany', *args, **kwargs)

    async def readexactly(self, *args, **kwargs):
        return await self._read('readexactly', *args, **kwargs)

    async def _read(self, method_name, *args, **kwargs):
        pin = Pin.get_from(self)
        # This may not have an immediate parent as the request completed
        with pin.tracer.trace('{}.{}'.format(self.__class__.__name__, method_name),
                              span_type=ext_http.TYPE, service=pin.service) as span:

            if self._self_parent_trace_id:
                span.trace_id = self._self_parent_trace_id

            if self._self_parent_span_id:
                span.parent_id = self._self_parent_span_id

            span.set_tags(pin.tags)
            span.resource = self._self_parent_resource

            result = await getattr(self.__wrapped__, method_name)(*args, **kwargs)
            span.set_tag('Length', len(result))

        return result


class _WrappedResponseClass(wrapt.ObjectProxy):
    def __init__(self, obj, pin):
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

    async def start(self, *args, **kwargs):
        # This will get called once per connect
        pin = Pin.get_from(self)

        # This will parent correctly as we'll always have an enclosing span
        with pin.tracer.trace('{}.start'.format(self.__class__.__name__),
                              span_type=ext_http.TYPE, service=pin.service) as span:
            _set_request_tags(span, _get_url_obj(self))

            wrapped = get_root_wrapped(self)
            await wrapped.start(*args, **kwargs)

            if pin._config["trace_headers"]:
                tags = {hdr: self.headers[hdr]
                        for hdr in self._self_trace_headers
                        if hdr in self.headers}
                span.set_tags(tags)
            else:
                tags = {}

            span.set_tag(ext_http.STATUS_CODE, self.status)
            span.set_tag(ext_http.METHOD, self.method)

            for tag in {ext_http.URL, ext_http.STATUS_CODE, ext_http.METHOD}:
                tags[tag] = span.get_tag(tag)

        # after start, self.__wrapped__.content is set
        pin = pin.clone(tags=tags)
        wrapped.content = _WrappedStreamReader(
            wrapped.content, pin, self._self_parent_trace_id, self._self_parent_span_id, span.resource)

        return self

    async def __aenter__(self):
        result = await self.__wrapped__.__aenter__()
        return result

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        result = await self.__wrapped__.__aexit__(exc_type, exc_val, exc_tb)
        return result


class _WrappedRequestContext(wrapt.ObjectProxy):
    def __init__(self, obj, pin, span):
        super().__init__(obj)
        pin.onto(self)
        self._self_span = span
        self._self_have_context = False

    async def _handle_response(self, coro):
        pin = Pin.get_from(self)
        try:
            resp = await coro

            if pin._config["trace_headers"]:
                tags = {hdr: resp.headers[hdr]
                        for hdr in pin._config["trace_headers"]
                        if hdr in resp.headers}
                self._self_span.set_tags(tags)

            self._self_span.set_tag(ext_http.STATUS_CODE, resp.status)
            self._self_span.error = int(500 <= resp.status)
            return resp
        except BaseException:
            self._self_span.set_traceback()
            raise
        finally:
            if not self._self_have_context or not pin._config["trace_context"]:
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
            if self._self_have_context:
                self._self_span.finish()
        return resp


def _create_wrapped_request(method, func, instance, args, kwargs):
    pin = Pin.get_from(instance)

    if not pin.tracer.enabled:
        return func(*args, **kwargs)

    if method == "REQUEST":
        url = URL(kwargs.get("url") or args[1])
    else:
        url = URL(kwargs.get("url") or args[0])

    if should_skip_request(pin, url):
        result = func(*args, **kwargs)
        return result

    # Create a new span and attach to this instance (so we can
    # retrieve/update/close later on the response)
    # Note that we aren't tracing redirects
    span = pin.tracer.trace('ClientSession.request', span_type=ext_http.TYPE, service=pin.service)

    if pin._config["distributed_tracing_enabled"]:
        headers = kwargs.get('headers', {})
        if headers is None:
            headers = {}
        propagator.inject(span.context, headers)
        kwargs["headers"] = headers

    _set_request_tags(span, url, kwargs.get('params'))
    span.set_tag(ext_http.METHOD, method)

    obj = _WrappedRequestContext(func(*args, **kwargs), pin, span)
    return obj


def _create_wrapped_response(client_session, cls, instance, args, kwargs):
    obj = _WrappedResponseClass(cls(*args, **kwargs), Pin.get_from(client_session))
    return obj


def _wrap_clientsession_init(func, instance, args, kwargs):
    # Use any attached tracer if available, otherwise use the global tracer
    pin = Pin.get_from(instance)

    if not pin.tracer.enabled:
        return func(*args, **kwargs)

    # note init doesn't really return anything
    ret = func(*args, **kwargs)

    # replace properties with our wrappers
    wrapper = functools.partial(_create_wrapped_response, instance)
    instance._response_class = wrapt.FunctionWrapper(instance._response_class, wrapper)

    instance._connector = _WrappedConnectorClass(instance._connector, pin)
    return ret


_clientsession_wrap_methods = {
    'get', 'options', 'head', 'post', 'put', 'patch', 'delete', 'request'
}


def patch():
    """
    Patch aiohttp third party modules:
        * aiohttp_jinja2
        * aiohttp ClientSession request
    """

    _w = wrapt.wrap_function_wrapper
    if not getattr(aiohttp, '__datadog_patch', False):
        setattr(aiohttp, '__datadog_patch', True)
        pin = Pin(config.aiohttp_client.service, app="aiohttp", _config=config.aiohttp_client)
        pin.onto(aiohttp.ClientSession)

        _w("aiohttp", "ClientSession.__init__", _wrap_clientsession_init)

        for method in _clientsession_wrap_methods:
            wrapper = functools.partial(_create_wrapped_request, method.upper())
            _w('aiohttp', 'ClientSession.{}'.format(method), wrapper)

    if _trace_render_template and not getattr(aiohttp_jinja2, '__datadog_patch', False):
        setattr(aiohttp_jinja2, '__datadog_patch', True)

        _w('aiohttp_jinja2', 'render_template', _trace_render_template)
        Pin(config.aiohttp_jinja.service, app="aiohttp", _config=config.aiohttp_jinja).onto(aiohttp_jinja2)


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
