from contextlib import contextmanager
import re
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Generator
from typing import Optional
from typing import Tuple
from typing import Union

from ddtrace.internal import compat
from ddtrace.internal.utils.cache import cached


Connector = Callable[[], ContextManager[compat.httplib.HTTPConnection]]


@cached()
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


def redact_query_string(query_string, query_string_obfuscation_pattern):
    # type: (str, Optional[re.Pattern]) -> Union[bytes, str]
    if query_string_obfuscation_pattern is None:
        return query_string

    bytes_query = query_string if isinstance(query_string, bytes) else query_string.encode("utf-8")
    return query_string_obfuscation_pattern.sub(b"<redacted>", bytes_query)


def redact_url(url, query_string_obfuscation_pattern, query_string=None):
    # type: (str, re.Pattern, Optional[str]) -> Union[str,bytes]

    # Avoid further processing if obfuscation is disabled
    if query_string_obfuscation_pattern is None:
        return url

    parts = compat.parse.urlparse(url)
    redacted_query = None

    if query_string:
        redacted_query = redact_query_string(query_string, query_string_obfuscation_pattern)
    elif parts.query:
        redacted_query = redact_query_string(parts.query, query_string_obfuscation_pattern)

    if redacted_query is not None and len(parts) >= 5:
        redacted_parts = parts[:4] + (redacted_query,) + parts[5:]  # type: Tuple[Union[str, bytes], ...]
        bytes_redacted_parts = tuple(x if isinstance(x, bytes) else x.encode("utf-8") for x in redacted_parts)
        return urlunsplit(bytes_redacted_parts, url)

    # If no obfuscation is performed, return original url
    return url


def urlunsplit(components, original_url):
    # type: (Tuple[bytes, ...], str) -> bytes
    """
    Adaptation from urlunsplit and urlunparse, using bytes components
    """
    scheme, netloc, url, params, query, fragment = components
    if params:
        url = b"%s;%s" % (url, params)
    if netloc or (scheme and url[:2] != b"//"):
        if url and url[:1] != b"/":
            url = b"/" + url
        url = b"//%s%s" % ((netloc or b""), url)
    if scheme:
        url = b"%s:%s" % (scheme, url)
    if query or (original_url and original_url[-1] in ("?", b"?")):
        url = b"%s?%s" % (url, query)
    if fragment or (original_url and original_url[-1] in ("#", b"#")):
        url = b"%s#%s" % (url, fragment)
    return url


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
