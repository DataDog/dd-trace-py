from contextlib import contextmanager
from json import loads
import logging
import os
import re
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Generator
from typing import Optional
from typing import Pattern
from typing import Tuple
from typing import Union

from ddtrace.constants import USER_ID_KEY
from ddtrace.internal import compat
from ddtrace.internal.compat import parse
from ddtrace.internal.constants import BLOCKED_RESPONSE_HTML
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import W3C_TRACESTATE_ORIGIN_KEY
from ddtrace.internal.constants import W3C_TRACESTATE_SAMPLING_PRIORITY_KEY
from ddtrace.internal.http import HTTPConnection
from ddtrace.internal.http import HTTPSConnection
from ddtrace.internal.uds import UDSHTTPConnection
from ddtrace.internal.utils import _get_metas_to_propagate
from ddtrace.internal.utils.cache import cached


ConnectionType = Union[HTTPSConnection, HTTPConnection, UDSHTTPConnection]


_W3C_TRACESTATE_INVALID_CHARS_REGEX_VALUE = re.compile(r",|;|~|[^\x20-\x7E]+")
_W3C_TRACESTATE_INVALID_CHARS_REGEX_KEY = re.compile(r",| |=|[^\x20-\x7E]+")

DEFAULT_TIMEOUT = 2.0


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

    @contextmanager
    def _connector_context():
        # type: () -> Generator[Union[compat.httplib.HTTPConnection, compat.httplib.HTTPSConnection], None, None]
        connection = get_connection(url, **kwargs)
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
    for k, v in _get_metas_to_propagate(context):
        if k not in [SAMPLING_DECISION_TRACE_TAG_KEY, USER_ID_KEY]:
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


class Response(object):
    """
    Custom API Response object to represent a response from calling the API.

    We do this to ensure we know expected properties will exist, and so we
    can call `resp.read()` and load the body once into an instance before we
    close the HTTPConnection used for the request.
    """

    __slots__ = ["status", "body", "reason", "msg"]

    def __init__(self, status=None, body=None, reason=None, msg=None):
        self.status = status
        self.body = body
        self.reason = reason
        self.msg = msg

    @classmethod
    def from_http_response(cls, resp):
        """
        Build a ``Response`` from the provided ``HTTPResponse`` object.

        This function will call `.read()` to consume the body of the ``HTTPResponse`` object.

        :param resp: ``HTTPResponse`` object to build the ``Response`` from
        :type resp: ``HTTPResponse``
        :rtype: ``Response``
        :returns: A new ``Response``
        """
        return cls(
            status=resp.status,
            body=resp.read(),
            reason=getattr(resp, "reason", None),
            msg=getattr(resp, "msg", None),
        )

    def get_json(self):
        """Helper to parse the body of this request as JSON"""
        try:
            body = self.body
            if not body:
                log.debug("Empty reply from Datadog Agent, %r", self)
                return

            if not isinstance(body, str) and hasattr(body, "decode"):
                body = body.decode("utf-8")

            if hasattr(body, "startswith") and body.startswith("OK"):
                # This typically happens when using a priority-sampling enabled
                # library with an outdated agent. It still works, but priority sampling
                # will probably send too many traces, so the next step is to upgrade agent.
                log.debug(
                    "Cannot parse Datadog Agent response. "
                    "This occurs because Datadog agent is out of date or DATADOG_PRIORITY_SAMPLING=false is set"
                )
                return

            return loads(body)
        except (ValueError, TypeError):
            log.debug("Unable to parse Datadog Agent JSON response: %r", body, exc_info=True)

    def __repr__(self):
        return "{0}(status={1!r}, body={2!r}, reason={3!r}, msg={4!r})".format(
            self.__class__.__name__,
            self.status,
            self.body,
            self.reason,
            self.msg,
        )


def get_connection(url, timeout=DEFAULT_TIMEOUT):
    # type: (str, float) -> ConnectionType
    """Return an HTTP connection to the given URL."""
    parsed = verify_url(url)
    hostname = parsed.hostname or ""
    path = parsed.path or "/"

    if parsed.scheme == "https":
        return HTTPSConnection.with_base_path(hostname, parsed.port, base_path=path, timeout=timeout)
    elif parsed.scheme == "http":
        return HTTPConnection.with_base_path(hostname, parsed.port, base_path=path, timeout=timeout)
    elif parsed.scheme == "unix":
        return UDSHTTPConnection(path, hostname, parsed.port, timeout=timeout)

    raise ValueError("Unsupported protocol '%s'" % parsed.scheme)


def verify_url(url):
    # type: (str) -> parse.ParseResult
    """Validates that the given URL can be used as an intake
    Returns a parse.ParseResult.
    Raises a ``ValueError`` if the URL cannot be used as an intake
    """
    parsed = parse.urlparse(url)
    schemes = ("http", "https", "unix")
    if parsed.scheme not in schemes:
        raise ValueError(
            "Unsupported protocol '%s' in intake URL '%s'. Must be one of: %s"
            % (parsed.scheme, url, ", ".join(schemes))
        )
    elif parsed.scheme in ["http", "https"] and not parsed.hostname:
        raise ValueError("Invalid hostname in intake URL '%s'" % url)
    elif parsed.scheme == "unix" and not parsed.path:
        raise ValueError("Invalid file path in intake URL '%s'" % url)

    return parsed


_HTML_BLOCKED_TEMPLATE_CACHE = None  # type: Optional[str]
_JSON_BLOCKED_TEMPLATE_CACHE = None  # type: Optional[str]


def _get_blocked_template(accept_header_value):
    # type: (str) -> str

    global _HTML_BLOCKED_TEMPLATE_CACHE
    global _JSON_BLOCKED_TEMPLATE_CACHE

    need_html_template = False

    if accept_header_value and "text/html" in accept_header_value.lower():
        need_html_template = True

    if need_html_template and _HTML_BLOCKED_TEMPLATE_CACHE:
        return _HTML_BLOCKED_TEMPLATE_CACHE

    if not need_html_template and _JSON_BLOCKED_TEMPLATE_CACHE:
        return _JSON_BLOCKED_TEMPLATE_CACHE

    if need_html_template:
        template_path = os.getenv("DD_APPSEC_HTTP_BLOCKED_TEMPLATE_HTML")
    else:
        template_path = os.getenv("DD_APPSEC_HTTP_BLOCKED_TEMPLATE_JSON")

    if template_path:
        try:
            with open(template_path, "r") as template_file:
                content = template_file.read()

            if need_html_template:
                _HTML_BLOCKED_TEMPLATE_CACHE = content
            else:
                _JSON_BLOCKED_TEMPLATE_CACHE = content
            return content
        except (OSError, IOError) as e:
            log.warning("Could not load custom template at %s: %s", template_path, str(e))  # noqa: G200

    # No user-defined template at this point
    if need_html_template:
        _HTML_BLOCKED_TEMPLATE_CACHE = BLOCKED_RESPONSE_HTML
        return BLOCKED_RESPONSE_HTML

    _JSON_BLOCKED_TEMPLATE_CACHE = BLOCKED_RESPONSE_JSON
    return BLOCKED_RESPONSE_JSON
