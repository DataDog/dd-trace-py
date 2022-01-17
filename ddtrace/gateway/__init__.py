from enum import Enum

from ddtrace.gateway.gateway import Gateway


gateway = Gateway()


class ADDRESSES(Enum):
    SERVER_REQUEST_BODY = "server.request.body"
    SERVER_REQUEST_QUERY = "server.request.query"
    SERVER_REQUEST_HEADERS_NO_COOKIES = "server.request.headers.no_cookies"
    SERVER_REQUEST_TRAILERS = "server.request.trailers"
    SERVER_REQUEST_URI_RAW = "server.request.uri.raw"
    SERVER_REQUEST_METHOD = "server.request.method"
    SERVER_REQUEST_PATH_PARAMS = "server.request.path_params"
    SERVER_REQUEST_COOKIES = "server.request.cookies"
    SERVER_RESPONSE_STATUS = "server.response.status"
    SERVER_RESPONSE_HEADERS_NO_COOKIES = "server.response.headers.no_cookies"
    SERVER_RESPONSE_TRAILERS = "server.response.trailers"


__all__ = ["ADDRESSES", "gateway"]
