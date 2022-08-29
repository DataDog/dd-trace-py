from contextlib import contextmanager
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Generator
from typing import Optional
from typing import Union

from ddtrace.internal import compat


Connector = Callable[[], ContextManager[compat.httplib.HTTPConnection]]


def normalize_header_name(header_name):
    # type: (Optional[str]) -> Optional[str]
    """
    Normalizes an header name to lower case, stripping all its leading and trailing white spaces.
    :param header_name: the header name to normalize
    :type header_name: str
    :return: the normalized header name
    :rtype: str
    """
    return header_name.strip().lower() if header_name is not None else None


def strip_query_string(url):
    # type: (str) -> str
    """
    Strips the query string from a URL for use as tag in spans.
    :param url: The URL to be stripped
    :return: The given URL without query strings
    """
    hqs, fs, f = url.partition("#")
    h, _, _ = hqs.partition("?")
    if not f:
        return h
    return h + fs + f


def connector(url, **kwargs):
    # type: (str, Any) -> Connector
    """Create a connector context manager for the given URL.

    This function returns a context manager that wraps a connection object to
    perform HTTP requests against the given URL. Extra keyword arguments can be
    passed to the underlying connection object, if needed.

    Example::
        >>> connect = connector("http://localhost:8080")
        >>> with connect() as conn:
        ...     conn.request("GET", "/")
        ...     ...
    """
    scheme = "http"
    if "://" in url:
        scheme, _, authority = url.partition("://")
    else:
        authority = url

    try:
        Connection = {
            "http": compat.httplib.HTTPConnection,
            "https": compat.httplib.HTTPSConnection,
            "unix": compat.httplib.HTTPConnection,
        }[scheme]
    except KeyError:
        raise ValueError("Unsupported scheme: %s" % scheme)

    host, _, _port = authority.partition(":")
    port = int(_port) if _port else None

    @contextmanager
    def _connector_context():
        # type: () -> Generator[Union[compat.httplib.HTTPConnection, compat.httplib.HTTPSConnection], None, None]
        connection = Connection(host, port, **kwargs)
        yield connection
        connection.close()

    return _connector_context
