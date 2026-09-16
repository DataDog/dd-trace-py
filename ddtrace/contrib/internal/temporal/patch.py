"""ddtrace patch support for the Temporal Python SDK.

The Temporal SDK is interceptor-based: users must pass a ``DatadogTracingInterceptor``
to ``Client.connect(..., interceptors=[...])``.  ``patch()`` therefore does
what monkey-patching can here: it wraps ``temporalio.client.Client.__init__``
so that every constructed client (including the one ``Client.connect`` builds
internally) gets a default ``DatadogTracingInterceptor`` appended to its
``interceptors`` list when one is not already present.

Constructing the interceptor also registers the ``ddtrace`` namespace as a
passthrough module with the Temporal workflow sandbox (see
``DatadogTracingInterceptor``), so ``patch(temporal=True)`` is sufficient for
fully functional workflow tracing.
"""

from typing import Any
from typing import Callable

import temporalio
import temporalio.client

from ddtrace.contrib.internal.temporal.interceptor import DatadogTracingInterceptor
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.settings._config import config


config._add(  # type: ignore[no-untyped-call]
    "temporal",
    dict(),
)


def get_version() -> str:
    return getattr(temporalio, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"temporalio": ">=1.0.0"}


def _traced_client_init(
    wrapped: Callable[..., Any], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    interceptors = list(kwargs.get("interceptors") or [])
    if not any(isinstance(i, DatadogTracingInterceptor) for i in interceptors):
        # service_name=None falls back to the global tracer service name.
        interceptors.append(DatadogTracingInterceptor(service_name=None))
        kwargs["interceptors"] = interceptors
    return wrapped(*args, **kwargs)


def patch() -> None:
    """Instrument the Temporal Python SDK.

    Wraps ``temporalio.client.Client.__init__`` so every client gets a default
    ``DatadogTracingInterceptor`` (unless one is already present).  The worker
    picks up client interceptors automatically.
    """
    if getattr(temporalio, "_datadog_patch", False):
        return
    temporalio._datadog_patch = True
    wrap("temporalio.client", "Client.__init__", _traced_client_init)


def unpatch() -> None:
    """Remove instrumentation from the Temporal Python SDK."""
    if not getattr(temporalio, "_datadog_patch", False):
        return
    temporalio._datadog_patch = False
    unwrap(temporalio.client.Client, "__init__")
