import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import Optional

from aiohttp import ClientError
from aiohttp import ClientSession
from aiohttp import ClientTimeout
from aiohttp import web
from yarl import URL

from ddtrace import config
import ddtrace.constants
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from ddtrace.trace import tracer


DOWNSTREAM_HTTP_TIMEOUT = 2.0

USERS = {
    "test": {"email": "testuser@ddog.com", "password": "1234", "name": "test", "id": "social-security-id"},
    "testuuid": {
        "email": "testuseruuid@ddog.com",
        "password": "1234",
        "name": "testuuid",
        "id": "591dc126-8431-4d0f-9509-b23318d3dce4",
    },
}


def _create_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users (id, name) VALUES ('1_secret_id', 'Alice')")
    db.execute("INSERT INTO users (id, name) VALUES ('2_secret_id', 'Bob')")
    db.execute("INSERT INTO users (id, name) VALUES ('3_secret_id', 'Christophe')")
    return db


def _set_rasp_done(endpoint: str) -> None:
    span = tracer.current_span()
    if span is not None and getattr(span, "_service_entry_span", None) is not None:
        span._service_entry_span.set_tag("rasp.request.done", endpoint)


def _html(text: str, status: int = 200) -> web.Response:
    return web.Response(text=text, status=status, content_type="text/html")


def _set_manual_priority(priority: Optional[str]) -> None:
    if priority in ("keep", "drop"):
        span = tracer.current_span()
        if span is not None:
            span.set_tag(ddtrace.constants.MANUAL_KEEP_KEY if priority == "keep" else ddtrace.constants.MANUAL_DROP_KEY)


@web.middleware
async def rename_service_middleware(request: web.Request, handler):
    if request.headers.get("x-rename-service", "false").lower() == "true":
        service_name = "sub-service"
        root_span = tracer.current_root_span()
        if root_span is not None:
            root_span.service = service_name
            root_span.set_tag("scope", service_name)
    return await handler(request)


async def home(request: web.Request) -> web.Response:
    return _html("ok ASM")


async def _asm_response(request: web.Request, path_params: dict) -> web.Response:
    query_params = dict(request.query)
    body = {
        "path_params": path_params,
        "query_params": query_params,
        "cookies": dict(request.cookies),
        "body": await request.text(),
        "method": request.method,
    }
    status = int(query_params.get("status", "200"))
    _set_manual_priority(query_params.get("priority", None))
    response_headers = {}
    for header in query_params.get("headers", "").split(","):
        vk = header.split("=")
        if len(vk) == 2:
            response_headers[vk[0]] = vk[1]
    return web.json_response(body, status=status, headers=response_headers)


async def asm(request: web.Request) -> web.Response:
    return await _asm_response(
        request,
        {"param_int": int(request.match_info["param_int"]), "param_str": request.match_info["param_str"]},
    )


async def asm_no_param(request: web.Request) -> web.Response:
    return await _asm_response(request, {"param_int": 0, "param_str": ""})


async def new_service(request: web.Request) -> web.Response:
    service_name = request.match_info["service_name"]
    config.aiohttp.service = service_name
    with tracer.start_span("span_with_new_service", service=service_name):
        # Generate a root span with the new service name. On span finish,
        # the service name will be added to the extra services queue.
        pass
    return _html(service_name)


async def stream(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/html"})
    await response.prepare(request)
    for number in range(10):
        await response.write(b"%d" % number)
        await asyncio.sleep(0.0625)
    await response.write_eof()
    return response


async def stream_upload(request: web.Request) -> web.Response:
    size = 0
    async for chunk in request.content.iter_any():
        size += len(chunk)
    return _html(str(size))


async def multipart(request: web.Request) -> web.Response:
    fields = {}
    reader = await request.multipart()
    while (part := await reader.next()) is not None:
        fields[part.name] = (await part.read()).decode()
    return web.json_response(fields)


async def stream_rasp(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/html"})
    await response.prepare(request)
    await response.write(b"start")
    try:
        with open(request.query["filename"], "rb"):
            pass
    except OSError:
        pass
    await response.write(b"end")
    await response.write_eof()
    return response


async def stream_headers(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "text/html"})
    for header in request.query.get("headers", "").split(","):
        vk = header.split("=")
        if len(vk) == 2:
            response.headers[vk[0]] = vk[1]
    await response.prepare(request)
    await response.write(b"0123456789")
    await response.write_eof()
    return response


async def block_request(request: web.Request) -> web.Response:
    from ddtrace.appsec.trace_utils import block_request as appsec_block_request

    appsec_block_request()
    return _html("not blocked")


async def set_user(request: web.Request) -> web.Response:
    from ddtrace.contrib.trace_utils import set_user as trace_set_user

    trace_set_user(tracer, request.query["user_id"])
    return _html("not blocked")


async def error(request: web.Request) -> web.Response:
    raise ValueError("unhandled error")


async def _ssrf(param: str, urlname: str) -> str:
    if param.startswith("url_urlopen_request"):
        import urllib.request

        request_urllib = urllib.request.Request(urlname, method="GET")
        with urllib.request.urlopen(request_urllib, timeout=0.5) as f:
            return f"Url: {f.read()}"
    if param.startswith("url_urlopen_string"):
        import urllib.request

        with urllib.request.urlopen(urlname, timeout=0.5) as f:
            return f"Url: {f.read()}"
    if param.startswith("url_requests"):
        import requests

        return f"Url: {requests.get(urlname, timeout=0.5).text}"
    if param.startswith("url_aiohttp"):
        async with ClientSession() as session, session.get(urlname, timeout=ClientTimeout(total=0.5)) as response:
            return f"Url: {await response.text()}"
    if param.startswith("url_httpx2_async"):
        import httpx2

        async with httpx2.AsyncClient() as client:
            return f"Url: {(await client.get(urlname, timeout=0.5)).text}"
    if param.startswith("url_httpx2"):
        import httpx2

        return f"Url: {httpx2.get(urlname, timeout=0.5).text}"
    if param.startswith("url_httpx_async"):
        import httpx

        async with httpx.AsyncClient() as client:
            return f"Url: {(await client.get(urlname, timeout=0.5)).text}"
    if param.startswith("url_httpx"):
        import httpx

        return f"Url: {httpx.get(urlname, timeout=0.5).text}"
    return ""


async def rasp(request: web.Request) -> web.Response:
    endpoint = request.match_info["endpoint"]
    query_params = dict(request.query)
    db = _create_db()
    try:
        if endpoint == "lfi":
            res = ["lfi endpoint"]
            filename = ""
            for param in query_params:
                if param.startswith("filename"):
                    filename = query_params[param]
                try:
                    if param.startswith("filename_pathlib"):
                        with Path(filename).open("rb") as f:
                            res.append(f"File (pathlib): {f.read()}")
                    else:
                        with open(filename, "rb") as f:
                            res.append(f"File: {f.read()}")
                except Exception as e:
                    res.append(f"Error: {e}")
        elif endpoint == "ssrf":
            res = ["ssrf endpoint"]
            for param in query_params:
                if param.startswith("url"):
                    urlname = query_params[param]
                    if not urlname.startswith("http"):
                        urlname = f"http://{urlname}"
                    try:
                        res.append(await _ssrf(param, urlname))
                    except Exception as e:
                        res.append(f"Error: {e}")
        elif endpoint == "sql_injection":
            res = ["sql_injection endpoint"]
            for param in query_params:
                if param.startswith("user_id"):
                    try:
                        cursor = db.execute(f"SELECT * FROM users WHERE id = {query_params[param]}")
                        res.append(f"Url: {list(cursor)}")
                    except Exception as e:
                        res.append(f"Error: {e}")
        elif endpoint == "shell_injection":
            res = ["shell_injection endpoint"]
            for param in query_params:
                if param.startswith("cmd"):
                    cmd = query_params[param]
                    try:
                        if param.startswith("cmdsys"):
                            res.append(f"cmd stdout: {os.system(f'ls {cmd}')}")
                        else:
                            res.append(f"cmd stdout: {subprocess.run(f'ls {cmd}', shell=True, timeout=1)}")
                    except Exception as e:
                        res.append(f"Error: {e}")
        elif endpoint == "command_injection":
            res = ["command_injection endpoint"]
            for param in query_params:
                cmd = query_params[param]
                try:
                    if param.startswith("cmda"):
                        res.append(f"cmd stdout: {subprocess.run([cmd, '-c', '3', 'localhost'], timeout=0.25)}")
                    elif param.startswith("cmds"):
                        res.append(f"cmd stdout: {subprocess.run(cmd, timeout=0.25)}")
                except Exception as e:
                    res.append(f"Error: {e}")
        else:
            res = [f"Unknown endpoint: {endpoint}"]
    finally:
        db.close()
    _set_rasp_done(endpoint)
    return _html("<\\br>\n".join(res))


def _downstream_url(request: web.Request) -> str:
    return f"http://127.0.0.1:{request.match_info['port']}/{request.match_info['route']}"


async def redirect(request: web.Request) -> web.Response:
    import urllib.request

    route = request.match_info["route"]
    try:
        if request.method == "POST":
            request_urllib = urllib.request.Request(
                _downstream_url(request),
                method="POST",
                data=await request.read(),
                headers={"Content-Type": "application/json", "TagRoute": route},
            )
        else:
            request_urllib = urllib.request.Request(_downstream_url(request), method="GET", headers={"TagRoute": route})
        with urllib.request.urlopen(request_urllib, timeout=DOWNSTREAM_HTTP_TIMEOUT) as f:
            payload = {"payload": f.read().decode("utf-8", errors="replace")}
    except Exception as e:
        payload = {"error": repr(e)}
    return web.json_response(payload)


async def redirect_requests(request: web.Request) -> web.Response:
    import requests

    route = request.match_info["route"]
    try:
        with requests.Session() as s:
            if request.method == "POST":
                response = s.post(
                    _downstream_url(request),
                    data=await request.read(),
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=DOWNSTREAM_HTTP_TIMEOUT,
                )
            else:
                response = s.get(_downstream_url(request), timeout=DOWNSTREAM_HTTP_TIMEOUT, headers={"TagRoute": route})
            payload = {"payload": response.text}
    except Exception as e:
        payload = {"error": repr(e)}
    return web.json_response(payload)


def _httpx_client_module(request: web.Request):
    if request.path.startswith("/redirect_httpx2"):
        import httpx2

        return httpx2
    import httpx

    return httpx


async def redirect_httpx(request: web.Request) -> web.Response:
    httpx_client = _httpx_client_module(request)
    route = request.match_info["route"]
    try:
        with httpx_client.Client() as client:
            if request.method == "POST":
                response = client.post(
                    _downstream_url(request),
                    content=await request.read(),
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=DOWNSTREAM_HTTP_TIMEOUT,
                    follow_redirects=True,
                )
            else:
                response = client.get(
                    _downstream_url(request),
                    timeout=DOWNSTREAM_HTTP_TIMEOUT,
                    headers={"TagRoute": route},
                    follow_redirects=True,
                )
            payload = {"payload": response.text}
    except Exception as e:
        payload = {"error": repr(e)}
    return web.json_response(payload)


async def redirect_httpx_async(request: web.Request) -> web.Response:
    httpx_client = _httpx_client_module(request)
    route = request.match_info["route"]
    try:
        async with httpx_client.AsyncClient() as client:
            if request.method == "POST":
                response = await client.post(
                    _downstream_url(request),
                    content=await request.read(),
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=DOWNSTREAM_HTTP_TIMEOUT,
                    follow_redirects=True,
                )
            else:
                response = await client.get(
                    _downstream_url(request),
                    timeout=DOWNSTREAM_HTTP_TIMEOUT,
                    headers={"TagRoute": route},
                    follow_redirects=True,
                )
            payload = {"payload": response.text}
    except Exception as e:
        payload = {"error": repr(e)}
    return web.json_response(payload)


async def redirect_aiohttp(request: web.Request) -> web.Response:
    route = request.match_info["route"]
    try:
        async with ClientSession(timeout=ClientTimeout(total=DOWNSTREAM_HTTP_TIMEOUT)) as session:
            if request.method == "POST":
                response = await session.post(
                    _downstream_url(request),
                    data=await request.read(),
                    headers={"Content-Type": "application/json", "TagRoute": route},
                )
            else:
                response = await session.get(_downstream_url(request), headers={"TagRoute": route})
            async with response:
                if request.query.get("stream"):
                    body = b"".join([chunk async for chunk in response.content.iter_any()])
                    payload = {"payload": body.decode("utf-8", errors="replace")}
                else:
                    payload = {"payload": await response.text()}
    except Exception as e:
        payload = {"error": repr(e)}
    return web.json_response(payload)


_STORED_RESPONSES: list = []


async def chunked_json(request: web.Request) -> web.StreamResponse:
    response = web.StreamResponse(headers={"Content-Type": "application/json"})
    response.enable_chunked_encoding()
    await response.prepare(request)
    await response.write(b'{"payload": "api10-response-body"}')
    await response.write_eof()
    return response


async def aiohttp_client_case(request: web.Request) -> web.Response:
    case = request.match_info["case"]
    target = f"http://127.0.0.1:{request.match_info['port']}/response-body"

    async def failover(req, handler):
        try:
            return await handler(req)
        except ClientError:
            req.url = URL(target)
            req.update_host(req.url)
            return await handler(req)

    async def nested(req, handler):
        async with req.session.get(target, middlewares=()) as response:
            await response.read()
        return await handler(req)

    if case == "read":
        session, response = _STORED_RESPONSES.pop()
        async with session:
            try:
                return _html(await response.text())
            except (ClientError, asyncio.TimeoutError) as e:
                return _html(repr(e), 502)
    middlewares = {"failover": (failover,), "nested": (nested,)}.get(case)
    timeout = (
        ClientTimeout(total=None, sock_connect=DOWNSTREAM_HTTP_TIMEOUT, sock_read=DOWNSTREAM_HTTP_TIMEOUT)
        if case == "store"
        else ClientTimeout(total=DOWNSTREAM_HTTP_TIMEOUT)
    )
    session: Optional[ClientSession] = ClientSession(
        timeout=timeout, **({"middlewares": middlewares} if middlewares else {})
    )
    url = {"failover": "http://127.0.0.1:1/", "chunked": f"http://127.0.0.1:{request.url.port}/chunked_json/"}
    try:
        response = await session.get(url.get(case, target))
        if case == "store":
            _STORED_RESPONSES.append((session, response))
            session = None
            return _html("stored")
        return _html(await response.text())
    except (ClientError, asyncio.TimeoutError) as e:
        return _html(repr(e), 502)
    finally:
        if session is not None:
            await session.close()


async def aiohttp_proxy(request: web.Request) -> web.Response:
    proxy = f"http://127.0.0.1:{request.match_info['port']}"
    try:
        async with ClientSession(timeout=ClientTimeout(total=DOWNSTREAM_HTTP_TIMEOUT)) as session:
            async with session.get("https://www.datadoghq.com/", proxy=proxy) as response:
                return _html(f"status {response.status}")
    except Exception as e:
        return _html(f"error {e.__class__.__name__}")


async def exception_group_block(request: web.Request) -> web.Response:
    """Endpoint to test that BlockingException wrapped in BaseExceptionGroup is properly handled."""
    if sys.version_info < (3, 11) or request.query.get("block") != "true":
        return _html("ok")

    from ddtrace.appsec._utils import Block_config
    from ddtrace.internal._exceptions import BlockingException

    raise BaseExceptionGroup("test", [BlockingException(Block_config())])  # noqa: F821


async def login(request: web.Request) -> web.Response:
    """manual instrumentation login endpoint"""
    from ddtrace.appsec import trace_utils as appsec_trace_utils

    def authenticate(username: str, password: str) -> Optional[str]:
        """authenticate user"""
        if username in USERS:
            if USERS[username]["password"] == password:
                return USERS[username]["id"]
            appsec_trace_utils.track_user_login_failure_event(
                tracer, user_id=USERS[username]["id"], exists=True, login_events_mode="auto", login=username
            )
            return None
        appsec_trace_utils.track_user_login_failure_event(
            tracer, user_id=username, exists=False, login_events_mode="auto", login=username
        )
        return None

    username = request.query.get("username", "")
    user_id = authenticate(username=username, password=request.query.get("password", ""))
    if user_id is not None:
        appsec_trace_utils.track_user_login_success_event(
            tracer, user_id=user_id, login_events_mode="auto", login=username
        )
        return _html("OK")
    return _html("login failure", status=401)


async def login_sdk(request: web.Request) -> web.Response:
    """manual instrumentation login endpoint using SDK V2"""
    try:
        from ddtrace.appsec import track_user_sdk
    except ImportError:
        return _html("SDK V2 not available", status=422)

    metadata = json.loads(request.query.get("metadata", "{}"))

    def authenticate(username: str, password: str) -> Optional[str]:
        """authenticate user"""
        if username in USERS:
            if USERS[username]["password"] == password:
                return USERS[username]["id"]
            track_user_sdk.track_login_failure(
                login=username, user_id=USERS[username]["id"], exists=True, metadata=metadata
            )
            return None
        track_user_sdk.track_login_failure(login=username, exists=False, metadata=metadata)
        return None

    username = request.query.get("username", "")
    user_id = authenticate(username=username, password=request.query.get("password", ""))
    if user_id is not None:
        track_user_sdk.track_login_success(login=username, user_id=user_id, metadata=metadata)
        return _html("OK")
    return _html("login failure", status=401)


def get_app() -> web.Application:
    app = web.Application(middlewares=[rename_service_middleware])
    for path, handler, methods in [
        ("/", home, ("GET", "POST", "OPTIONS")),
        (r"/asm/{param_int:\d+}/{param_str}", asm, ("GET", "POST")),
        (r"/asm/{param_int:\d+}/{param_str}/", asm, ("GET", "POST")),
        ("/asm", asm_no_param, ("GET", "POST")),
        ("/asm/", asm_no_param, ("GET", "POST")),
        ("/new_service/{service_name}", new_service, ("GET", "POST")),
        ("/new_service/{service_name}/", new_service, ("GET", "POST")),
        ("/stream/", stream, ("GET",)),
        ("/rasp/{endpoint}/", rasp, ("GET", "POST", "OPTIONS")),
        (r"/redirect/{route}/{port:\d+}", redirect, ("GET", "POST")),
        (r"/redirect_requests/{route}/{port:\d+}", redirect_requests, ("GET", "POST")),
        (r"/redirect_httpx/{route}/{port:\d+}", redirect_httpx, ("GET", "POST")),
        (r"/redirect_httpx2/{route}/{port:\d+}", redirect_httpx, ("GET", "POST")),
        (r"/redirect_httpx_async/{route}/{port:\d+}", redirect_httpx_async, ("GET", "POST")),
        (r"/redirect_httpx2_async/{route}/{port:\d+}", redirect_httpx_async, ("GET", "POST")),
        (r"/redirect_aiohttp/{route}/{port:\d+}", redirect_aiohttp, ("GET", "POST")),
        (r"/aiohttp_proxy/{port:\d+}", aiohttp_proxy, ("GET",)),
        (r"/aiohttp_client/{case}/{port:\d+}", aiohttp_client_case, ("GET",)),
        (r"/chunked_json/", chunked_json, ("GET",)),
        ("/exception-group-block", exception_group_block, ("GET",)),
        ("/login/", login, ("GET",)),
        ("/login_sdk/", login_sdk, ("GET",)),
        ("/stream_upload/", stream_upload, ("POST",)),
        ("/multipart/", multipart, ("POST",)),
        ("/stream_rasp/", stream_rasp, ("GET",)),
        ("/stream_headers/", stream_headers, ("GET",)),
        ("/block_request/", block_request, ("GET",)),
        ("/set_user/", set_user, ("GET",)),
        ("/error/", error, ("GET",)),
    ]:
        for method in methods:
            app.router.add_route(method, path, handler)
    trace_app(app)
    return app
