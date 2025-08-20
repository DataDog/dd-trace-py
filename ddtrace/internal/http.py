import http.client as httplib
from urllib import parse

from ddtrace.internal.runtime import container


class HTTPConnectionMixin:
    """
    Mixin for HTTP(S) connections for performing internal adjustments.

    Currently this mixin performs the following adjustments:
    - insert a base path to requested URLs
    - update headers with container info
    """

    _base_path: str = "/"

    def putrequest(self, method: str, url: str, skip_host: bool = False, skip_accept_encoding: bool = False) -> None:
        url = parse.urljoin(self._base_path, url)
        return super().putrequest(  # type: ignore[misc]
            method, url, skip_host=skip_host, skip_accept_encoding=skip_accept_encoding
        )

    @classmethod
    def with_base_path(cls, *args, **kwargs):
        base_path = kwargs.pop("base_path", None)
        obj = cls(*args, **kwargs)
        obj._base_path = base_path
        return obj

    def request(self, method, url, body=None, headers={}, *, encode_chunked=False):
        _headers = headers.copy()

        container.update_headers(_headers)

        return super().request(method, url, body=body, headers=_headers, encode_chunked=encode_chunked)


class HTTPConnection(HTTPConnectionMixin, httplib.HTTPConnection):
    """
    httplib.HTTPConnection wrapper to add a base path to requested URLs
    """


class HTTPSConnection(HTTPConnectionMixin, httplib.HTTPSConnection):
    """
    httplib.HTTPSConnection wrapper to add a base path to requested URLs
    """
