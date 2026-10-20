"""Wire the opt-in Starlette proof into the shared HTTP/ASGI lifecycle.

Use an unpatched Starlette app. This prototype is not a replacement for patch():
see README.md for unsupported compatibility and security paths.
"""

from typing import Any

from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.starlette.lifecycle import StarletteLifecycle
from ddtrace.internal.settings.integration import IntegrationConfig


def wire_starlette(
    app: Any, *, integration_config: IntegrationConfig, service: str = "starlette-proof"
) -> StarletteLifecycle:
    """Return an ASGI callable; close it to restore the app's route hooks."""
    settings = HttpLifecycleConfig("starlette", service, integration_config)
    return StarletteLifecycle(app, settings)
