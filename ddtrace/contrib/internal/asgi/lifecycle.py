"""Opt-in HTTP contract scaffold, independent of ASGI tracing and security products."""

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import replace
from typing import Any
from typing import Optional
from urllib.parse import parse_qsl

from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestDataEvent
from ddtrace.contrib._events.http import HttpRequestError
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib._events.http import HttpRequestSnapshot
from ddtrace.contrib._events.http import HttpResponseData
from ddtrace.internal import core


_SCOPE_KEY = "datadog.http_lifecycle"
_Message = dict[str, Any]
_Receive = Callable[[], Awaitable[_Message]]
_Send = Callable[[_Message], Awaitable[None]]
_App = Callable[[_Message, _Receive, _Send], Awaitable[None]]


def _headers(message: _Message) -> tuple[tuple[str, str], ...]:
    return tuple((name.decode("latin-1"), value.decode("latin-1")) for name, value in message.get("headers", ()))


class _Lifecycle:
    def __init__(self, scope: _Message, framework: str) -> None:
        headers = _headers(scope)
        host = next((value for name, value in headers if name.lower() == "host"), None)
        if host is None:
            server = scope.get("server")
            host = str(server[0]) if server else ""
            if server and server[1] is not None:
                if ":" in host:
                    host = f"[{host}]"
                host += f":{server[1]}"
        query = scope.get("query_string", b"").decode("latin-1")
        raw_path = scope.get("raw_path")
        path = raw_path.decode("latin-1") if raw_path is not None else scope.get("path", "/")
        self.request = HttpRequestData(
            method=scope.get("method", ""),
            url=f"{scope.get('scheme', 'http')}://{host}{path}" + (f"?{query}" if query else ""),
            headers=headers,
            query_string=query,
            query_parameters=tuple(parse_qsl(query, keep_blank_values=True)),
        )
        self.framework = framework
        self.request_id = object()
        self.response: Optional[HttpResponseData] = None
        self.errors: tuple[HttpRequestError, ...] = ()
        self.committed = False
        self.handler_started = False
        self.paths: list[str] = []
        self.seen: set[int] = set()

    def snapshot(self, phase: HttpRequestPhase) -> HttpRequestSnapshot:
        return HttpRequestSnapshot(
            request_id=self.request_id,
            framework=self.framework,
            phase=phase,
            request=self.request,
            response=self.response,
            errors=self.errors,
            handler_started=self.handler_started,
            response_committed=self.committed,
        )

    def publish(self, phase: HttpRequestPhase) -> None:
        if core.has_listeners(HttpRequestDataEvent.event_name):
            core.dispatch_event(HttpRequestDataEvent(snapshot=self.snapshot(phase)))

    def route(self, route: Any, final: bool, effective_path: Optional[str]) -> None:
        if id(route) in self.seen:
            return
        self.seen.add(id(route))
        self.paths.append(route.path)
        if final:
            self.request = replace(self.request, route=effective_path or "".join(self.paths))
            self.publish(HttpRequestPhase.ROUTE_RESOLVED)

    def before_handler(self, route: Any) -> None:
        if not self.handler_started and (not route.methods or self.request.method in route.methods):
            self.publish(HttpRequestPhase.BEFORE_HANDLER)
            self.handler_started = True


class HttpLifecycleMiddleware:
    """Wrap an application explicitly to exercise the HTTP contracts without creating spans."""

    def __init__(self, app: _App, framework: str = "asgi") -> None:
        self.app = app
        self.framework = framework

    async def __call__(self, scope: _Message, receive: _Receive, send: _Send) -> None:
        if scope["type"] != "http" or _SCOPE_KEY in scope:
            await self.app(scope, receive, send)
            return
        lifecycle = _Lifecycle(scope, self.framework)
        scope[_SCOPE_KEY] = lifecycle

        async def wrapped_send(message: _Message) -> None:
            if message["type"] == "http.response.start":
                lifecycle.response = HttpResponseData(message["status"], _headers(message))
                lifecycle.publish(HttpRequestPhase.RESPONSE_READY)
                await send(message)
                lifecycle.committed = True
                lifecycle.publish(HttpRequestPhase.RESPONSE_COMMITTED)
            else:
                await send(message)

        try:
            lifecycle.publish(HttpRequestPhase.REQUEST_STARTED)
            await self.app(scope, receive, wrapped_send)
        except BaseException as error:
            try:
                message = str(error)
            except BaseException:
                message = None
            lifecycle.errors += (HttpRequestError(type(error).__name__, message),)
            raise
        finally:
            try:
                lifecycle.publish(HttpRequestPhase.COMPLETED)
            finally:
                scope.pop(_SCOPE_KEY, None)
