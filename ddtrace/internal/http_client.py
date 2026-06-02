"""The native ``HTTPClient`` with the process-wide shared runtime auto-injected.

The native :class:`ddtrace.internal.native.HTTPClient` (a PyO3 class) owns the
base URL, default headers, timeout, and request methods. It requires a
``SharedRuntime`` to be passed explicitly so the native crate never has to reach
back into Python for the singleton. This subclass injects that runtime in
``__new__`` so callers get a clean interface:

    from ddtrace.internal.http_client import HTTPClient

    client = HTTPClient("http://localhost:8126", headers={"Datadog-Meta-Lang": "python"})
    info = json.loads(client.get("/info").body())
    client.post("/v0.4/traces", headers={"Content-Type": "application/msgpack"}, body=payload)

All request behavior (base-URL join, header merge, transport, errors) lives in
the native class — this subclass adds nothing but the runtime wiring, so there is
a single surface to test.

DEV: the runtime must be injected in ``__new__``, not ``__init__`` — PyO3's
``#[new]`` maps to ``__new__``, which runs (and builds the Rust object) before
any Python ``__init__``.
"""

from ddtrace.internal.native import HTTPClient as _HTTPClient
from ddtrace.internal.native_runtime import get_native_runtime


class HTTPClient(_HTTPClient):
    def __new__(cls, *args, **kwargs):
        return super().__new__(cls, *args, runtime=get_native_runtime(), **kwargs)
