import functools
import logging

from ddtrace import config
from ddtrace.vendor import wrapt
from ddtrace.internal.logger import get_logger

from ...utils.wrappers import get_root_wrapped
from ...propagation.http import HTTPPropagator
from ...pin import Pin
from ...ext import http as ext_http
from ..httplib.patch import should_skip_request
from ..trace_utils import unwrap
from ..trace_utils import wrap

from yarl import URL


log = get_logger(__name__)

# Server config
config._add(
    "aiohttp",
    dict(distributed_tracing=True),
)

config._add(
    "aiohttp_client",
    dict(
        distributed_tracing=asbool(os.getenv("DD_AIOHTTP_CLIENT_DISTRIBUTED_TRACING", True)),
        default_http_tag_query_string=os.getenv("DD_HTTP_CLIENT_TAG_QUERY_STRING", "true"),
        redact_query_keys=set(),

    ),
)

# Set these on the ClientSession instance to override the settings
# from the patch method
ENABLE_DISTRIBUTED_ATTR_NAME = '_dd_enable_distributed'
TRACE_HEADERS_ATTR_NAME = '_dd_trace_headers'


def _set_request_tags(span: Span, req: Union[ClientRequest, ClientResponse]):
    url_str = str(req.url)
    parsed_url = parse.urlparse(url_str)

    set_http_meta(
        span,
        config.aiohttp_client,
        method=req.method,
        url=url_str,
        query=parsed_url.query,
        request_headers=req.headers,
    )



class _WrappedConnectorClass(wrapt.ObjectProxy):
    def __init__(self, obj, pin):
        super().__init__(obj)
        pin.onto(self)

    async def connect(self, req, *args, **kwargs):
        pin = Pin.get_from(self)
        with pin.tracer.trace("%s.connect" % self.__class__.__name__,
                              span_type=ext_http.TYPE, service=pin.service) as span:
            _set_request_tags(span, req)
            # We call this way so "self" will not get sliced and call
            # _create_connection on us first
            result = await self.__wrapped__.__class__.connect(self, req, *args, **kwargs)
            return result

    async def _create_connection(self, req, *args, **kwargs):
        pin = Pin.get_from(self)
        with pin.tracer.trace(
                "%s._create_connection" % self.__class__.__name__,
                span_type=ext_http.TYPE, service=pin.service) as span:
            _set_request_tags(span, req)
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
            _set_request_tags(span, self)

            wrapped = get_root_wrapped(self)
            await wrapped.start(*args, **kwargs)

            set_http_meta(
                span,
                config.aiohttp_client,
                response_headers=self.headers,
                status_code=self.status,
                status_msg=self.reason
            )

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


@with_traced_module
async def _traced_clientsession_request(aiohttp, pin, func, instance, args, kwargs):
    method = get_argument_value(args, kwargs, 0, "method")  # type: str
    url = URL(get_argument_value(args, kwargs, 1, "url"))  # type: URL
    params = kwargs.get("params")
    headers = kwargs.get("headers") or {}

    if should_skip_request(pin, url):
        result = func(*args, **kwargs)
        return result

    # Create a new span and attach to this instance (so we can
    # retrieve/update/close later on the response)
    # Note that we aren't tracing redirects
    with pin.tracer.trace(
        "aiohttp.request", span_type=SpanTypes.HTTP, service=ext_service(pin, config.aiohttp_client)
    ) as span:
        enabled_distributed = pin._config["distributed_tracing"]
        if hasattr(instance, ENABLE_DISTRIBUTED_ATTR_NAME):
            enable_distributed |= getattr(instance, ENABLE_DISTRIBUTED_ATTR_NAME)

        if enabled_distributed:
            HTTPPropagator.inject(span.context, headers)
            kwargs["headers"] = headers

        # Params can be included separate of the URL so the URL has to be constructed
        # with the passed params.
        url_str = str(url.update_query(params) if params else url)
        parsed_url = parse.urlparse(url_str)
        set_http_meta(
            span,
            config.aiohttp_client,
            method=method,
            url=url_str,
            query=parsed_url.query,
            request_headers=headers,
        )

        resp = await func(*args, **kwargs)  # type: aiohttp.ClientResponse
        set_http_meta(
            span, config.aiohttp_client, response_headers=resp.headers, status_code=resp.status, status_msg=resp.reason
        )
        return resp


def _create_wrapped_response(client_session, cls, instance, args, kwargs):
    obj = _WrappedResponseClass(cls(*args, **kwargs), Pin.get_from(client_session), trace_headers)
    return obj


@with_traced_module_sync
def _traced_clientsession_init(aiohttp, pin, func, instance, args, kwargs):
    func(*args, **kwargs)
    wrapper = functools.partial(_create_wrapped_response, instance)
    instance._response_class = wrapt.FunctionWrapper(instance._response_class, wrapper)
    instance._connector = _WrappedConnectorClass(instance._connector, pin)


def _patch_client(aiohttp):
    Pin().onto(aiohttp)
    pin = Pin(_config=config.aiohttp_client.copy())
    pin.onto(aiohttp.ClientSession)

    wrap("aiohttp", "ClientSession.__init__", _traced_clientsession_init(aiohttp))
    wrap("aiohttp", "ClientSession._request", _traced_clientsession_request(aiohttp))


def patch():
    import aiohttp

    if getattr(aiohttp, "_datadog_patch", False):
        return

    _patch_client(aiohttp)

    setattr(aiohttp, "_datadog_patch", True)


def _unpatch_client(aiohttp):
    unwrap(aiohttp.ClientSession, "__init__")
    unwrap(aiohttp.ClientSession, "_request")


def unpatch():
    import aiohttp

    if not getattr(aiohttp, "_datadog_patch", False):
        return

    _unpatch_client(aiohttp)

    setattr(aiohttp, "__datadog_patch", False)
