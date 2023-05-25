import os

from ddtrace._tracing._limits import MAX_SPAN_META_VALUE_LEN
from ddtrace.appsec._asm_request_context import add_context_callback
from ddtrace.appsec._asm_request_context import remove_context_callback
from ddtrace.appsec._constants import SPAN_DATA_NAMES

from .schema import get_json_schema


COLLECTED = [
    (SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES, "_dd.schema.req.headers"),
    (SPAN_DATA_NAMES.REQUEST_QUERY, "_dd.schema.req.query"),
    (SPAN_DATA_NAMES.REQUEST_PATH_PARAMS, "_dd.schema.req.params"),
    (SPAN_DATA_NAMES.REQUEST_BODY, "_dd.schema.req.body"),
    (SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES, "_dd.schema.res.headers"),
    (SPAN_DATA_NAMES.RESPONSE_BODY, "_dd.schema.res.body"),
]


def flask_api_schema_callback(env):
    waf_content = env.waf_addresses
    root = env.span._local_root or env.span
    if not root:
        return
    for addresses, meta_name in COLLECTED:
        value = waf_content.get(addresses, None)
        if value:
            json_serialized = get_json_schema(value)
            if len(json_serialized) >= MAX_SPAN_META_VALUE_LEN:
                json_serialized = "schema too large"
            root._meta[meta_name] = get_json_schema(value)
    route = waf_content.get(SPAN_DATA_NAMES.REQUEST_ROUTE, None) or ""
    if route is not None:
        root._meta[SPAN_DATA_NAMES.REQUEST_ROUTE] = route


def enable_api_security():
    if "_DD_API_SECURITY" in os.environ:
        add_context_callback(flask_api_schema_callback, global_callback=True)


def disable_api_security():
    if "_DD_API_SECURITY" in os.environ:
        remove_context_callback(flask_api_schema_callback, global_callback=True)
