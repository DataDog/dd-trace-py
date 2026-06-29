from typing import Any

from .http import NativeHTTPConnection


class UDSHTTPConnection(NativeHTTPConnection):
    """An HTTP connection established over a Unix Domain Socket.

    Backed by the native Rust HTTP client (unix:// scheme) so it is unaffected
    by gevent monkey-patching.
    """

    # Keep host/port in the constructor signature for callers that pass them
    # (they are forwarded as the HTTP Host header by the Rust client).
    def __init__(self, path: str, *_args: Any, timeout: float = 2.0, **_kwargs: Any) -> None:
        self.path = path
        super().__init__(f"unix://{path}", timeout)
