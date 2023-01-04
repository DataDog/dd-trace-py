from contextlib import contextmanager
import logging
import re
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Generator
from typing import Optional
from typing import Pattern
from typing import Tuple
from typing import Union

import six

from ddtrace.constants import USER_ID_KEY
from ddtrace.internal import compat
from ddtrace.internal.constants import W3C_TRACESTATE_ORIGIN_KEY
from ddtrace.internal.constants import W3C_TRACESTATE_SAMPLING_PRIORITY_KEY
from ddtrace.internal.sampling import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.utils.cache import cached


_W3C_TRACESTATE_INVALID_CHARS_REGEX_VALUE = re.compile(r",|;|~|[^\x20-\x7E]+")
_W3C_TRACESTATE_INVALID_CHARS_REGEX_KEY = re.compile(r",| |=|[^\x20-\x7E]+")


Connector = Callable[[], ContextManager[compat.httplib.HTTPConnection]]


log = logging.getLogger(__name__)


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


def w3c_get_dd_list_member(context):
    # Context -> str
    tags = []
    if context.sampling_priority is not None:
        tags.append("{}:{}".format(W3C_TRACESTATE_SAMPLING_PRIORITY_KEY, context.sampling_priority))
    if context.dd_origin:
        tags.append(
            "{}:{}".format(
                W3C_TRACESTATE_ORIGIN_KEY,
                w3c_encode_tag((_W3C_TRACESTATE_INVALID_CHARS_REGEX_VALUE, "_", context.dd_origin)),
            )
        )

    sampling_decision = context._meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)
    if sampling_decision:
        tags.append(
            "t.dm:{}".format((w3c_encode_tag((_W3C_TRACESTATE_INVALID_CHARS_REGEX_VALUE, "_", sampling_decision))))
        )
    # since this can change, we need to grab the value off the current span
    usr_id = context._meta.get(USER_ID_KEY)
    if usr_id:
        tags.append("t.usr.id:{}".format(w3c_encode_tag((_W3C_TRACESTATE_INVALID_CHARS_REGEX_VALUE, "_", usr_id))))

    current_tags_len = sum(len(i) for i in tags)
    for k, v in context._meta.items():
        if (
            isinstance(k, six.string_types)
            and k.startswith("_dd.p.")
            # we've already added sampling decision and user id
            and k not in [SAMPLING_DECISION_TRACE_TAG_KEY, USER_ID_KEY]
        ):
            # for key replace ",", "=", and characters outside the ASCII range 0x20 to 0x7E
            # for value replace ",", ";", "~" and characters outside the ASCII range 0x20 to 0x7E
            k = k.replace("_dd.p.", "t.")
            next_tag = "{}:{}".format(
                w3c_encode_tag((_W3C_TRACESTATE_INVALID_CHARS_REGEX_KEY, "_", k)),
                w3c_encode_tag((_W3C_TRACESTATE_INVALID_CHARS_REGEX_VALUE, "_", v)),
            )
            # we need to keep the total length under 256 char
            potential_current_tags_len = current_tags_len + len(next_tag)
            if not potential_current_tags_len > 256:
                tags.append(next_tag)
                current_tags_len += len(next_tag)
            else:
                log.debug("tracestate would exceed 256 char limit with tag: %s. Tag will not be added.", next_tag)

    return ";".join(tags)


@cached()
def w3c_encode_tag(args):
    # type: (Tuple[Pattern, str, str]) -> str
    pattern, replacement, tag_val = args
    tag_val = pattern.sub(replacement, tag_val)
    # replace = with ~ if it wasn't already replaced by the regex
    return tag_val.replace("=", "~")
