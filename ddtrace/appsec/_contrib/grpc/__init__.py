from ddtrace.appsec._asm_request_context import set_headers
from ddtrace.appsec._asm_request_context import set_waf_address
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.internal import core


def _on_grpc_server_response(message):
    set_waf_address(SPAN_DATA_NAMES.GRPC_SERVER_RESPONSE_MESSAGE, message)


def _on_grpc_server_data(headers, request_message, method, metadata):
    set_headers(headers)
    if request_message is not None:
        set_waf_address(SPAN_DATA_NAMES.GRPC_SERVER_REQUEST_MESSAGE, request_message)

    set_waf_address(SPAN_DATA_NAMES.GRPC_SERVER_METHOD, method)

    if metadata:
        set_waf_address(SPAN_DATA_NAMES.GRPC_SERVER_REQUEST_METADATA, dict(metadata))


def listen() -> None:
    core.on("grpc.server.response.message", _on_grpc_server_response)
    core.on("grpc.server.data", _on_grpc_server_data)
