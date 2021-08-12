try:
    from collections.abc import Iterable as ABCIterable
    from collections.abc import Mapping as ABCMapping
except ImportError:
    from collections import Mapping as ABCMapping
    from collections import Iterable as ABCIterable

from collections import defaultdict
from typing import Iterator
from typing import List
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import TYPE_CHECKING
from typing import Tuple
from typing import Union

import six

from ddtrace.internal.compat import to_unicode
from ddtrace.utils.cache import cached


WSGI_HTTP_PREFIX = "HTTP_"
# PEP 333 gives two headers which aren't prepended with HTTP_.
WSGI_UNPREFIXED_HEADERS = {"CONTENT_TYPE", "CONTENT_LENGTH"}


if TYPE_CHECKING:

    class BaseHeaders(Mapping[str, Sequence[str]]):
        """Base class for HTTP headers."""


else:

    class BaseHeaders(ABCMapping):
        """Base class for HTTP headers."""


AnyHeaders = Union[
    Mapping[str, str],
    Mapping[str, Sequence[str]],
    Sequence[Tuple[str, str]],
    BaseHeaders,
]


def normalize_header_name(header_name):
    # type: (Optional[str]) -> Optional[str]
    """
    Normalizes an header name to lower case, stripping all its leading and trailing white spaces.
    :param header_name: the header name to normalize
    :type header_name: str
    :return: the normalized header name
    :rtype: str
    """
    if header_name is not None:
        return _normalize_header_name(header_name)
    return None


def _normalize_header_name(header_name):
    # type: (str) -> str
    return header_name.strip().lower()


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


class Headers(BaseHeaders):
    """
    Lazy normalization of HTTP headers.

    This class accepts any input data compatible with AnyHeaders and normalizes it.
    The header names are converted to lowercase, leading and ending whitespaces are
    stripped. Header lookups are case-insensitives and headers enumeration returns
    lowercase names.

    The headers are converted only when accessed.
    """

    def __init__(self, headers):
        # type: (AnyHeaders) -> None
        if isinstance(headers, BaseHeaders):
            # DEV: chained objects, can we do better?
            self._normalized_headers = headers  # type: Mapping[str, Sequence[str]]
        elif hasattr(headers, "items"):
            self._it = six.iteritems(headers)  # type: ignore[arg-type]
        else:
            self._it = iter(headers)  # type: ignore[arg-type]

    @property
    def normalized_headers(self):
        # type: () -> Mapping[str, Sequence[str]]
        headers = defaultdict(list)  # type: Mapping[str, List[str]]
        for key, value in self._it:
            # value should be either a string or a list of string
            # but in practice it can be anything
            if not isinstance(value, six.string_types) and isinstance(value, ABCIterable):
                headers[_normalize_header_name(key)].extend([to_unicode(v) for v in value])
            else:
                headers[_normalize_header_name(key)].append(to_unicode(value))  # type: ignore[type-var]
        # freeze the headers dict otherwise lookups create empty values
        return dict(headers)

    @property
    def cached_normalized_headers(self):
        # type: () -> Mapping[str, Sequence[str]]
        headers = getattr(self, "_normalized_headers", None)
        if headers is None:
            self._normalized_headers = headers = self.normalized_headers
        return headers

    def __getitem__(self, header_name):
        # type: (str) -> Sequence[str]
        return self.cached_normalized_headers[_normalize_header_name(header_name)]

    def __iter__(self):
        # type: () -> Iterator[str]
        return iter(self.cached_normalized_headers)

    def __len__(self):
        # type: () -> int
        return len(self.cached_normalized_headers)


@cached()
def from_wsgi_header(header):
    # type: (str) -> Optional[str]
    """Convert a WSGI compliant HTTP header into the original header.
    See https://www.python.org/dev/peps/pep-3333/#environ-variables for
    information from the spec.
    """

    if header.startswith(WSGI_HTTP_PREFIX):
        header = header[len(WSGI_HTTP_PREFIX) :]
    elif header not in WSGI_UNPREFIXED_HEADERS:
        return None
    return header.replace("_", "-").title()


class WSGIHeaders(Headers):
    """
    Lazy transformation of WSGI environ into normalized HTTP headers.
    """

    def __init__(self, environ):
        # type: (Mapping[str, str]) -> None
        self.environ = environ

    @property
    def normalized_headers(self):
        # type: () -> Mapping[str, Sequence[str]]
        headers = {}
        for key, value in self.environ.items():
            header_name = from_wsgi_header(key)
            if header_name is not None:
                headers[_normalize_header_name(header_name)] = [value]
        return headers
