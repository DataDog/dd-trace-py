from ddtrace.internal.compat import httplib
from ddtrace.internal.compat import parse


class BasePathMixin(httplib.HTTPConnection, object):
    """
    Mixin for HTTPConnection to insert a base path to requested URLs
    """

    _base_path = "/"  # type: str

    def putrequest(self, method, url, skip_host=False, skip_accept_encoding=False):
        # type: (str, str, bool, bool) -> None
        url = parse.urljoin(self._base_path, url)
        return super(BasePathMixin, self).putrequest(
            method, url, skip_host=skip_host, skip_accept_encoding=skip_accept_encoding
        )

    @classmethod
    def with_base_path(cls, *args, **kwargs):
        base_path = kwargs.pop("base_path", None)
        obj = cls(*args, **kwargs)
        obj._base_path = base_path
        return obj


class HTTPConnection(BasePathMixin, httplib.HTTPConnection):
    """
    httplib.HTTPConnection wrapper to add a base path to requested URLs
    """


class HTTPSConnection(BasePathMixin, httplib.HTTPSConnection):
    """
    httplib.HTTPSConnection wrapper to add a base path to requested URLs
    """
