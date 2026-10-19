"""Wire framework hooks into the shared HTTP controller and WSGI driver.

This example uses Bottle's production hooks. Another WSGI integration supplies
request_data, blocking_response, and its own hook installation. Its routing and
handler hooks call driver.current(environ) and pass HTTP values to the controller.
Consumers still receive push-only events; controller methods are producer APIs.

ASGI needs a separate async transport driver for receive/send, cancellation, and
activation across awaits. The synchronous WSGI driver must not wrap coroutines.
"""

from typing import Any
from typing import Optional

from ddtrace import config
from ddtrace.contrib.internal.bottle.lifecycle import BottleHooks
from ddtrace.contrib.internal.bottle.trace import TracePlugin
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.http_wsgi import WsgiLifecycleDriver


def install_bottle(app: Any) -> None:
    """Illustrate TracePlugin.setup wiring; do not install twice on a live app."""

    def get_config() -> Optional[HttpLifecycleConfig]:
        plugin = next((p for p in reversed(app.plugins) if isinstance(p, TracePlugin)), None)
        if plugin is None:
            return None
        return HttpLifecycleConfig("bottle", plugin.service, config.bottle)

    hooks = BottleHooks()
    driver = WsgiLifecycleDriver(hooks=hooks, get_config=get_config)
    hooks.install(app, driver.current)
    app.__dict__["wsgi"] = driver.wrap(app.wsgi)
