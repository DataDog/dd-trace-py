"""Wire the opt-in Starlette proof into the shared HTTP/ASGI lifecycle.

Use an unpatched Starlette app. This prototype is not a replacement for patch():
see README.md for unsupported compatibility and security paths.
"""

from contextlib import AbstractContextManager
from typing import Any

from ddtrace.contrib.internal.http_asgi import Application
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.starlette.patch import instrument_http
from ddtrace.internal.settings.integration import IntegrationConfig


def wire_starlette(
    app: Any, *, integration_config: IntegrationConfig, service: str = "starlette-proof"
) -> AbstractContextManager[Application]:
    """Use as a context manager yielding the instrumented ASGI callable."""
    settings = HttpLifecycleConfig("starlette", service, integration_config)
    return instrument_http(app, settings)
