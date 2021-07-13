from typing import Optional

from ddtrace.internal.compat import httplib
from ddtrace.internal.compat import parse
from ddtrace.utils.cache import cached


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


@cached()
def get_wsgi_header(header):
    # type: (str) -> str
    """Returns a WSGI compliant HTTP header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """
    return "HTTP_{}".format(header.upper().replace("-", "_"))


@cached()
def from_wsgi_header(header):
    # type: (str) -> Optional[str]
    """Convert a WSGI compliant HTTP header into the original header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """
    HTTP_PREFIX = "HTTP_"
    # PEP 333 gives two headers which aren't prepended with HTTP_.
    UNPREFIXED_HEADERS = {"CONTENT_TYPE", "CONTENT_LENGTH"}

    if header.startswith(HTTP_PREFIX):
        header = header[len(HTTP_PREFIX) :]
    elif header not in UNPREFIXED_HEADERS:
        return None
    return header.replace("_", "-").title()
