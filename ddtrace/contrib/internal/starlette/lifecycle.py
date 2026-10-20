"""Opt-in Starlette adapter; does not replace the public tracing middleware."""

from typing import Any
from typing import Optional
from urllib.parse import parse_qsl

from starlette.datastructures import URL
from starlette.routing import Mount
from starlette.routing import Route

from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib.internal.http_asgi import Application
from ddtrace.contrib.internal.http_asgi import AsgiLifecycleDriver
from ddtrace.contrib.internal.http_asgi import Receive
from ddtrace.contrib.internal.http_asgi import Scope
from ddtrace.contrib.internal.http_asgi import Send
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig


class StarletteHooks:
    def request_data(self, scope: Scope) -> HttpRequestData:
        headers = tuple((key.decode("latin-1"), value.decode("latin-1")) for key, value in scope.get("headers", ()))
        query = scope.get("query_string", b"").decode("latin-1")
        url = str(URL(scope=scope))
        return HttpRequestData(scope["method"], url, headers, query, tuple(parse_qsl(query, keep_blank_values=True)))

    def fallback_route(self, scope: Scope) -> Optional[tuple[str, str]]:
        # This proof reports only routes actually selected by Starlette. Speculative
        # route matching for middleware short circuits remains a separate concern.
        return None


class StarletteLifecycle:
    """Explicit installation for an unpatched app, with reversible route hooks.

    One outer lifecycle observes mounted routes. Nested application spans, FastAPI
    coordination, and public middleware callbacks are outside this prototype.
    """

    def __init__(self, app: Any, settings: HttpLifecycleConfig) -> None:
        self.driver = AsgiLifecycleDriver(hooks=StarletteHooks(), get_config=lambda: settings)
        self.originals: list[tuple[Any, Application, Application]] = []
        self._install(app.routes, "")
        self.application = self.driver.wrap(app)

    def _install(self, routes: Any, prefix: str) -> None:
        for route in routes:
            if isinstance(route, Mount):
                self._install(route.routes, prefix + route.path)
            elif isinstance(route, Route):
                original = route.app
                wrapper = self._wrap_route(original, prefix + route.path, route.path)
                self.originals.append((route, original, wrapper))
                route.app = wrapper

    def _wrap_route(self, app: Application, full_path: str, pattern: str) -> Application:
        async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
            request = self.driver.current(scope)
            if request is not None:
                lifecycle = request.lifecycle
                block = lifecycle.route_resolved(full_path, pattern)
                if block is not None:
                    await request.enforce_block(block)
                block = lifecycle.before_handler()
                if block is not None:
                    await request.enforce_block(block)
            await app(scope, receive, send)

        return wrapped

    def close(self) -> None:
        for route, original, wrapper in reversed(self.originals):
            if route.app is wrapper:
                route.app = original
        self.originals.clear()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.application(scope, receive, send)
