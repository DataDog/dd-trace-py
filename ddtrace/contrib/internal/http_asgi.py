"""Opt-in ASGI HTTP driver for the shared lifecycle."""

from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import replace
import sys
from typing import Any
from typing import Optional
from typing import Protocol

from ddtrace.contrib._events.http import HttpBlockingResponse
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpResponseData
from ddtrace.contrib.internal.http_server import HttpLifecycle
from ddtrace.contrib.internal.http_server import HttpLifecycleConfig
from ddtrace.contrib.internal.http_server import has_http_listeners


Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
Application = Callable[[Scope, Receive, Send], Awaitable[None]]
_REQUEST = "datadog.http.asgi.request"
_TRANSPORT_HEADERS = frozenset(("connection", "transfer-encoding", "content-length", "trailer", "upgrade"))


class AsgiFrameworkHooks(Protocol):
    def request_data(self, scope: Scope) -> HttpRequestData: ...

    def fallback_route(self, scope: Scope) -> Optional[tuple[str, str]]: ...


class _Blocked(BaseException):
    """Unwind application execution after a replacement has been delivered."""


def _only_blocked(error: BaseException) -> bool:
    # Task groups can wrap our control signal. Never swallow unrelated failures.
    children = getattr(error, "exceptions", None)
    return isinstance(error, _Blocked) or bool(children and all(_only_blocked(child) for child in children))


def _valid_block(block: HttpBlockingResponse) -> bool:
    return all(name.isascii() and name.lower() not in _TRANSPORT_HEADERS for name, _ in block.headers)


class AsgiLifecycleDriver:
    def __init__(self, *, hooks: AsgiFrameworkHooks, get_config: Callable[[], Optional[HttpLifecycleConfig]]) -> None:
        self.hooks = hooks
        self.get_config = get_config

    def current(self, scope: Scope) -> Optional["AsgiRequest"]:
        request = scope.get(_REQUEST)
        return request if isinstance(request, AsgiRequest) and request.driver is self else None

    def wrap(self, app: Application) -> Application:
        async def wrapped(scope: Scope, receive: Receive, send: Send) -> None:
            settings = self.get_config() if scope["type"] == "http" else None
            if settings is None or not has_http_listeners():
                await app(scope, receive, send)
                return
            if _REQUEST in scope:
                raise RuntimeError("nested ASGI lifecycle drivers are not supported by this prototype")
            lifecycle = HttpLifecycle(
                replace(settings, finish_on_response=True), self.hooks.request_data(scope), validate_block=_valid_block
            )
            request = AsgiRequest(self, lifecycle, scope, receive, send)
            scope[_REQUEST] = request
            try:
                await request.run(app)
            finally:
                scope.pop(_REQUEST, None)

        return wrapped


class AsgiRequest:
    def __init__(
        self, driver: AsgiLifecycleDriver, lifecycle: HttpLifecycle, scope: Scope, receive: Receive, send: Send
    ) -> None:
        self.driver = driver
        self.lifecycle = lifecycle
        self.scope = scope
        self.original_scope = dict(scope)
        self.receive = receive
        self.downstream_send = send
        self.trailers = False
        self.delivered = False
        self.body_complete = False
        self.replaced = False

    def complete(self) -> None:
        lifecycle = self.lifecycle
        if lifecycle.completed:
            return
        if lifecycle.request_data.route is None:
            route = self.driver.hooks.fallback_route(self.original_scope)
            if route is not None:
                lifecycle.request_data = replace(lifecycle.request_data, route=route[0], route_pattern=route[1])
        lifecycle.complete()

    async def run(self, app: Application) -> None:
        lifecycle = self.lifecycle
        try:
            block = lifecycle.start()
            if block is not None:
                await self.enforce_block(block)
            await app(self.scope, self.receive, self.send)
            if not self.delivered:
                raise RuntimeError("ASGI application returned without completing its response")
        except BaseException as error:
            if not _only_blocked(error):
                lifecycle.record_exception(sys.exc_info())
                raise
        finally:
            try:
                self.complete()
            finally:
                lifecycle.finish()

    async def enforce_block(self, block: HttpBlockingResponse) -> None:
        if self.lifecycle.committed:
            return
        self.replaced = True
        headers = [(name.lower().encode("ascii"), value.encode("latin-1")) for name, value in block.headers]
        if block.status_code not in (204, 304):
            headers.append((b"content-length", str(len(block.body)).encode("ascii")))
        response = HttpResponseData(
            block.status_code, tuple((k.decode("ascii"), v.decode("latin-1")) for k, v in headers)
        )
        self.lifecycle.prepare_response(response)
        self.lifecycle.delivery_attempted(response)
        await self.downstream_send({"type": "http.response.start", "status": block.status_code, "headers": headers})
        self.lifecycle.response_committed()
        body = b"" if self.lifecycle.request_data.method == "HEAD" else block.body
        await self.downstream_send({"type": "http.response.body", "body": body, "more_body": False})
        self.delivered = True
        self.complete()
        raise _Blocked()

    async def send(self, message: Message) -> None:
        if self.replaced:
            raise _Blocked()
        lifecycle = self.lifecycle
        kind = message["type"]
        if self.delivered:
            raise RuntimeError("ASGI response already complete")
        if kind not in ("http.response.start", "http.response.body", "http.response.trailers"):
            raise NotImplementedError(f"ASGI response extension is not supported: {kind}")
        if kind == "http.response.start":
            if lifecycle.committed:
                raise RuntimeError("ASGI response already committed")
            response = HttpResponseData(
                message["status"],
                tuple((k.decode("latin-1"), v.decode("latin-1")) for k, v in message.get("headers", ())),
            )
            block = lifecycle.prepare_response(response)
            if block is not None:
                await self.enforce_block(block)
            lifecycle.delivery_attempted(response)
            await self.downstream_send(message)
            self.trailers = message.get("trailers", False)
            lifecycle.response_committed()
            return
        if not lifecycle.committed:
            raise RuntimeError("ASGI response body sent before response start")
        if kind == "http.response.body" and self.body_complete:
            raise RuntimeError("ASGI response body already complete")
        if kind == "http.response.trailers" and (not self.trailers or not self.body_complete):
            raise RuntimeError("ASGI trailers sent without an announced, completed body")
        await self.downstream_send(message)
        if kind == "http.response.body" and not message.get("more_body", False):
            self.body_complete = True
        terminal = (kind == "http.response.body" and not message.get("more_body", False) and not self.trailers) or (
            kind == "http.response.trailers" and not message.get("more_trailers", False)
        )
        if terminal:
            self.delivered = True
            # Error responses wait for application unwind to capture the traceback.
            if lifecycle.response_data is not None and (lifecycle.response_data.status_code or 500) < 500:
                self.complete()
