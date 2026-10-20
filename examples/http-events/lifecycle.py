"""Wrap a WSGI app without implementing a framework adapter or lifecycle class.

Framework routing hooks can optionally use driver.current(environ) to publish a
matched route or a before-handler check. The driver handles request extraction,
blocking responses, activation, delivery, iteration, and completion itself.
"""

from collections.abc import Callable
from typing import Any

from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.http_wsgi import WsgiLifecycleDriver


def instrument_wsgi(application: Callable[..., Any], settings: HttpLifecycleConfig) -> Callable[..., Any]:
    driver = WsgiLifecycleDriver(get_config=lambda: settings)
    return driver.wrap(application)
