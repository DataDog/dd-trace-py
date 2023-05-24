import os

from ddtrace.appsec._asm_request_context import add_context_callback
from ddtrace.appsec._asm_request_context import remove_context_callback
from ddtrace.appsec._constants import SPAN_DATA_NAMES

from .schema import get_json_schema


COLLECTED = [
    SPAN_DATA_NAMES.REQUEST_HEADERS_NO_COOKIES,
    SPAN_DATA_NAMES.REQUEST_QUERY,
    SPAN_DATA_NAMES.REQUEST_PATH_PARAMS,
    SPAN_DATA_NAMES.REQUEST_BODY,
    SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES,
    SPAN_DATA_NAMES.RESPONSE_BODY,
]


def flask_api_schema_callback(env):
    waf_content = env.waf_addresses
    api_content = {}
    for addresses in COLLECTED:
        value = waf_content.get(addresses, None)
        if value:
            api_content[addresses] = get_json_schema(value)
    root = env.span._local_root or env.span
    root._meta["_dd.api_security"] = str(api_content)


def enable_api_security():
    if "_DD_API_SECURITY" in os.environ:
        add_context_callback(flask_api_schema_callback, global_callback=True)


def disable_api_security():
    if "_DD_API_SECURITY" in os.environ:
        remove_context_callback(flask_api_schema_callback, global_callback=True)
