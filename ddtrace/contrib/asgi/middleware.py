from ddtrace import tracer as global_tracer
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.ext import SpanTypes, http
from ddtrace.http import store_request_headers, store_response_headers
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.settings import config
from ddtrace.vendor.six.moves import urllib

config._add("asgi", dict(service_name=config._get_service(default="asgi"), distributed_tracing=True,))


def _extract_tags_from_scope(scope):
    server = scope.get("server") or ["0.0.0.0", 80]
    port = server[1]
    server_host = server[0] + (":" + str(port) if port != 80 else "")
    full_path = scope.get("root_path", "") + scope.get("path", "")
    http_url = scope.get("scheme", "http") + "://" + server_host + full_path

    query_string = scope.get("query_string")

    if query_string and http_url:
        if isinstance(query_string, bytes):
            query_string = query_string.decode("utf8")
        http_url = http_url + ("?" + urllib.parse.unquote(query_string))

    http_method = scope.get("method")

    tags = {
        http.URL: http_url,
        http.METHOD: http_method,
    }

    if query_string is not None and len(query_string) > 0:
        tags[http.QUERY_STRING] = query_string

    return tags


def _get_headers_dict_from_scope(scope):
    return {k: v for (k, v) in scope["headers"]}


class TraceMiddleware:
    def __init__(self, app, tracer=global_tracer):
        self.app = app
        self.tracer = tracer

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        headers = _get_headers_dict_from_scope(scope)

        if config.asgi.distributed_tracing:
            propagator = HTTPPropagator()
            context = propagator.extract(headers)
            if context.trace_id:
                self.tracer.context_provider.activate(context)

        resource = "{} {}".format(scope["method"], scope["path"])

        span = self.tracer.trace(
            name="asgi.request", service=config.asgi.service_name, resource=resource, span_type=SpanTypes.HTTP,
        )

        sample_rate = config.asgi.get_analytics_sample_rate(use_global_config=True)
        if sample_rate is not None:
            span.set_tag(ANALYTICS_SAMPLE_RATE_KEY, sample_rate)

        tags = _extract_tags_from_scope(scope)
        span.set_tags(tags)

        store_request_headers(headers, span, config.asgi)

        async def wrapped_send(message):
            span = self.tracer.current_span()

            if span and message.get("type") == "http.response.start":
                if "status" in message:
                    status_code = message["status"]
                    span.set_tag(http.STATUS_CODE, status_code)
                if "headers" in message:
                    store_response_headers(message["headers"], span, config.asgi)

            return await send(message)

        try:
            return await self.app(scope, receive, wrapped_send)
        except BaseException as exc:
            span.set_traceback()
            raise exc from None
        finally:
            span.finish()
