from typing import List
from typing import Mapping
from typing import Optional
from typing import TYPE_CHECKING
from typing import Union

from ._hooks import Hooks


if TYPE_CHECKING:
    from ddtrace.span import Span


_HTTP_HOOKS = Hooks()

REQUEST = "request"
RESPONSE = "response"


def emit_http_request(
    span,         # type: Span
    integration,  # type: str
    method,       # type: str
    url,          # type: str
    headers,      # type: Mapping[str, str]
    query=None,   # type: Optional[Mapping[str, List[str]]]
):
    """
    Notify registered listeners about an HTTP request received
    by an instrumented framework. It is emitted before the HTTP
    response event.

    Integration-specific listeners are notified before global listeners.
    """
    # Integration-specific hook listeners
    _HTTP_HOOKS.emit(
        (integration, REQUEST),
        span,
        integration,
        method,
        url,
        headers,
        query=query,
    )
    # Global hook listeners
    _HTTP_HOOKS.emit(
        (None, REQUEST), span, integration, method, url, headers, query=query
    )


def register_http_request(f, integration=None):
    _HTTP_HOOKS.register((integration, REQUEST), f)


def deregister_http_request(f, integration=None):
    _HTTP_HOOKS.deregister((integration, REQUEST), f)


def emit_http_response(
    span,             # type: Span
    integration,      # type: str
    status_code,      # type: Union[int, str]
    headers,          # type: Mapping[str, str]
    status_msg=None,  # type: Optional[str]
):
    """
    Notify registered listeners about an HTTP response sent
    by an instrumented framework. It is emitted after the HTTP
    request event.

    Integration-specific listeners are notified before global listeners.
    """
    # Integration-specific hook listeners
    _HTTP_HOOKS.emit(
        (integration, RESPONSE),
        span,
        integration,
        status_code,
        headers,
        status_msg=status_msg,
    )
    # Global hook listeners
    _HTTP_HOOKS.emit(
        (None, RESPONSE),
        span,
        integration,
        status_code,
        headers,
        status_msg=status_msg,
    )


def register_http_response(f, integration=None):
    _HTTP_HOOKS.register((integration, RESPONSE), f)


def deregister_http_response(f, integration=None):
    _HTTP_HOOKS.deregister((integration, RESPONSE), f)
