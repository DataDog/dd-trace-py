"""The native ``HTTPClient`` with the process-wide shared runtime auto-injected.

The native :class:`ddtrace.internal.native.HTTPClient` (a PyO3 class) owns the
base URL, default headers, timeout, and request methods. It requires a
``SharedRuntime`` to be passed explicitly so the native crate never has to reach
back into Python for the singleton. This subclass injects that runtime in
``__new__`` so callers get a clean interface:

    from ddtrace.internal.http_client import HTTPClient

    client = HTTPClient("http://localhost:8126", headers=[("Datadog-Meta-Lang", "python")])
    info = json.loads(client.get("/info").body())
    client.post("/v0.4/traces", headers=[("Content-Type", "application/msgpack")], body=payload)

All request behavior (base-URL join, header merge, transport, errors) lives in
the native class — this subclass only injects the shared runtime.

DEV: the runtime must be injected in ``__new__``, not ``__init__`` — PyO3's
``#[new]`` maps to ``__new__``, which runs (and builds the Rust object) before
any Python ``__init__``.
"""

from __future__ import annotations

from collections.abc import Iterable

from ddtrace.internal.native import HTTPClient as _HTTPClient
from ddtrace.internal.native_runtime import get_native_runtime


class HTTPClient(_HTTPClient):
    def __new__(
        cls,
        base_url: str,
        *,
        timeout_ms: int = 2000,
        headers: Iterable[tuple[str, str]] | None = None,
        max_retries: int = 0,
        retry_initial_delay_ms: int = 100,
        retry_jitter: bool = True,
        treat_http_errors_as_errors: bool = True,
    ) -> "HTTPClient":
        return super().__new__(
            cls,
            base_url,
            runtime=get_native_runtime(),
            timeout_ms=timeout_ms,
            headers=headers,
            max_retries=max_retries,
            retry_initial_delay_ms=retry_initial_delay_ms,
            retry_jitter=retry_jitter,
            treat_http_errors_as_errors=treat_http_errors_as_errors,
        )
