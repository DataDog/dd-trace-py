from enum import Enum
from typing import TYPE_CHECKING

import attr


if TYPE_CHECKING:
    from ddtrace.span import _RequestStore


class _Addresses(Enum):
    SERVER_REQUEST_BODY = "server.request.body"
    SERVER_REQUEST_QUERY = "server.request.query"
    SERVER_REQUEST_HEADERS_NO_COOKIES = "server.request.headers.no_cookies"
    # SERVER_REQUEST_TRAILERS = "server.request.trailers"
    SERVER_REQUEST_URI_RAW = "server.request.uri.raw"
    SERVER_REQUEST_METHOD = "server.request.method"
    SERVER_REQUEST_PATH_PARAMS = "server.request.path_params"
    SERVER_REQUEST_COOKIES = "server.request.cookies"
    SERVER_RESPONSE_STATUS = "server.response.status"
    SERVER_RESPONSE_HEADERS_NO_COOKIES = "server.response.headers.no_cookies"
    # SERVER_RESPONSE_TRAILERS = "server.response.trailers"


@attr.s(eq=False)
class _Gateway(object):

    _addresses_to_keep = attr.ib(type=set[str], factory=set)

    def clear(self):
        self._addresses_to_keep.clear()

    @property
    def needed_address_count(self):
        # type: () -> int
        return len(self._addresses_to_keep)

    def is_needed(self, address):
        # type: (str) -> bool
        return address in self._addresses_to_keep

    def mark_needed(self, address):
        # type: (str) -> None
        self._addresses_to_keep.add(address)

    def propagate(self, store, data):
        # type: (_RequestStore, dict) -> None
        for key in data.keys():
            if key in self._addresses_to_keep:
                store.kept_addresses[key] = data[key]
