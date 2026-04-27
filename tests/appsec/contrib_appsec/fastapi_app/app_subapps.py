"""Sub-application variant of the FastAPI test app.

All endpoints are identical to app.py but grouped endpoints are mounted
as separate FastAPI sub-applications instead of being registered directly
on the main app. This exercises the sub-application code paths in the
tracing and AppSec integrations.
"""

import asyncio
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from typing import AsyncGenerator
from typing import Optional

from fastapi import Depends
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse

from ddtrace import config
import ddtrace.constants
from ddtrace.trace import tracer


DOWNSTREAM_HTTP_TIMEOUT = 2.0


def get_app_with_subapps():
    app = FastAPI()

    async def get_db() -> AsyncGenerator[sqlite3.Connection, None]:
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO users (id, name) VALUES ('1_secret_id', 'Alice')")
        db.execute("INSERT INTO users (id, name) VALUES ('2_secret_id', 'Bob')")
        db.execute("INSERT INTO users (id, name) VALUES ('3_secret_id', 'Christophe')")
        try:
            yield db
        finally:
            db.close()

    @app.middleware("http")
    async def passthrough_middleware(request: Request, call_next):
        """Middleware to test BlockingException nesting in ExceptionGroups (or BaseExceptionGroups)

        With middlewares, the BlockingException can become nested multiple levels deep inside
        an ExceptionGroup (or BaseExceptionGroup). The nesting depends the version of FastAPI
        and AnyIO used, as well as the version of python.
        By adding this empty middleware, we ensure that the BlockingException is caught
        no matter how deep the ExceptionGroup is nested or else the contrib tests fail.
        """
        return await call_next(request)

    @app.middleware("http")
    async def rename_service(request: Request, call_next):
        if request.headers.get("x-rename-service", "false").lower() == "true":
            service_name = "sub-service"
            root_span = tracer.current_root_span()
            if root_span is not None:
                root_span.service = service_name
                root_span.set_tag("scope", service_name)

        return await call_next(request)

    # --- Main app endpoints (no prefix group) ---

    @app.get("/", response_class=HTMLResponse, status_code=200)
    @app.post("/", response_class=HTMLResponse, status_code=200)
    @app.options("/", response_class=HTMLResponse, status_code=200)
    async def read_homepage():  # noqa: B008
        return "ok ASM"

    @app.get("/exception-group-block")
    async def exception_group_block(request: Request):
        """Endpoint to test that BlockingException wrapped in BaseExceptionGroup is properly handled."""
        if sys.version_info < (3, 11) or request.query_params.get("block") != "true":
            return HTMLResponse("ok", status_code=200)

        from ddtrace.appsec._utils import Block_config
        from ddtrace.internal._exceptions import BlockingException

        raise BaseExceptionGroup("test", [BlockingException(Block_config())])  # noqa: F821

    # --- /asm sub-application ---

    asm_app = FastAPI()

    @asm_app.get("/{param_int:int}/{param_str:str}/", response_class=JSONResponse)
    @asm_app.post("/{param_int:int}/{param_str:str}/", response_class=JSONResponse)
    @asm_app.get("/{param_int:int}/{param_str:str}", response_class=JSONResponse)
    @asm_app.post("/{param_int:int}/{param_str:str}", response_class=JSONResponse)
    async def multi_view(param_int: int, param_str: str, request: Request):  # noqa: B008
        query_params = dict(request.query_params)
        body = {
            "path_params": {"param_int": param_int, "param_str": param_str},
            "query_params": query_params,
            "cookies": dict(request.cookies),
            "body": (await request.body()).decode("utf-8"),
            "method": request.method,
        }
        status = int(query_params.get("status", "200"))
        headers_query = query_params.get("headers", "").split(",")
        priority = query_params.get("priority", None)
        if priority in ("keep", "drop"):
            tracer.current_span().set_tag(
                ddtrace.constants.MANUAL_KEEP_KEY if priority == "keep" else ddtrace.constants.MANUAL_DROP_KEY
            )
        response_headers = {}
        for header in headers_query:
            vk = header.split("=")
            if len(vk) == 2:
                response_headers[vk[0]] = vk[1]
        return JSONResponse(body, status_code=status, headers=response_headers)

    @asm_app.get("/", response_class=JSONResponse)
    @asm_app.post("/", response_class=JSONResponse)
    async def multi_view_no_param(request: Request):  # noqa: B008
        query_params = dict(request.query_params)
        body = {
            "path_params": {"param_int": 0, "param_str": ""},
            "query_params": query_params,
            "headers": dict(request.headers),
            "cookies": dict(request.cookies),
            "body": (await request.body()).decode("utf-8"),
            "method": request.method,
        }
        status = int(query_params.get("status", "200"))
        headers_query = query_params.get("headers", "").split(",")
        priority = query_params.get("priority", None)
        if priority in ("keep", "drop"):
            tracer.current_span().set_tag(
                ddtrace.constants.MANUAL_KEEP_KEY if priority == "keep" else ddtrace.constants.MANUAL_DROP_KEY
            )
        response_headers = {}
        for header in headers_query:
            vk = header.split("=")
            if len(vk) == 2:
                response_headers[vk[0]] = vk[1]
        return JSONResponse(body, status_code=status, headers=response_headers)

    app.mount("/asm", asm_app)

    # --- /new_service sub-application ---

    new_service_app = FastAPI()

    @new_service_app.get("/{service_name:str}/")
    @new_service_app.post("/{service_name:str}/")
    @new_service_app.get("/{service_name:str}")
    @new_service_app.post("/{service_name:str}")
    async def new_service(service_name: str, request: Request):  # noqa: B008
        config.fastapi.service = service_name
        with tracer.start_span("span_with_new_service", service=service_name):
            pass
        return HTMLResponse(service_name, 200)

    app.mount("/new_service", new_service_app)

    # --- /stream sub-application ---

    stream_app = FastAPI()

    async def slow_numbers(minimum, maximum):
        for number in range(minimum, maximum):
            yield "%d" % number
            await asyncio.sleep(0.0625)

    @stream_app.get("/")
    async def stream():
        return StreamingResponse(slow_numbers(0, 10), media_type="text/html")

    app.mount("/stream", stream_app)

    # --- /rasp sub-application ---

    rasp_app = FastAPI()

    @rasp_app.get("/{endpoint:str}/")
    @rasp_app.post("/{endpoint:str}/")
    @rasp_app.options("/{endpoint:str}/")
    async def rasp(endpoint: str, request: Request, db: sqlite3.Connection = Depends(get_db)):
        query_params = request.query_params
        if endpoint == "lfi":
            res = ["lfi endpoint"]
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
            tracer.current_span()._service_entry_span.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\br>\n".join(res))
        elif endpoint == "ssrf":
            res = ["ssrf endpoint"]
            for param in query_params:
                urlname = ""
                if param.startswith("url"):
                    urlname = query_params[param]
                    if not urlname.startswith("http"):
                        urlname = f"http://{urlname}"
                try:
                    if param.startswith("url_urlopen_request"):
                        import urllib.request

                        request_urllib = urllib.request.Request(urlname, method="GET")
                        with urllib.request.urlopen(request_urllib, timeout=0.5) as f:
                            res.append(f"Url: {f.read()}")
                    elif param.startswith("url_urlopen_string"):
                        import urllib.request

                        with urllib.request.urlopen(urlname, timeout=0.5) as f:
                            res.append(f"Url: {f.read()}")
                    elif param.startswith("url_requests"):
                        import requests

                        r = requests.get(urlname, timeout=0.5)
                        res.append(f"Url: {r.text}")
                    elif param.startswith("url_httpx_async"):
                        import httpx

                        async with httpx.AsyncClient() as client:
                            r = await client.get(urlname, timeout=0.5)
                            res.append(f"Url: {r.text}")
                    elif param.startswith("url_httpx"):
                        import httpx

                        r = httpx.get(urlname, timeout=0.5)
                        res.append(f"Url: {r.text}")
                except Exception as e:
                    res.append(f"Error: {e}")
            tracer.current_span()._service_entry_span.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
        elif endpoint == "sql_injection":
            res = ["sql_injection endpoint"]
            for param in query_params:
                if param.startswith("user_id"):
                    user_id = query_params[param]
                try:
                    if param.startswith("user_id"):
                        cursor = db.execute(f"SELECT * FROM users WHERE id = {user_id}")
                        res.append(f"Url: {list(cursor)}")
                except Exception as e:
                    res.append(f"Error: {e}")
            tracer.current_span()._service_entry_span.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
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
            tracer.current_span()._service_entry_span.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
        elif endpoint == "command_injection":
            res = ["command_injection endpoint"]
            for param in query_params:
                if param.startswith("cmda"):
                    cmd = query_params[param]
                    try:
                        res.append(f"cmd stdout: {subprocess.run([cmd, '-c', '3', 'localhost'], timeout=0.25)}")
                    except Exception as e:
                        res.append(f"Error: {e}")
                elif param.startswith("cmds"):
                    cmd = query_params[param]
                    try:
                        res.append(f"cmd stdout: {subprocess.run(cmd, timeout=0.25)}")
                    except Exception as e:
                        res.append(f"Error: {e}")
            tracer.current_span()._service_entry_span.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
        tracer.current_span()._service_entry_span.set_tag("rasp.request.done", endpoint)
        return HTMLResponse(f"Unknown endpoint: {endpoint}")

    app.mount("/rasp", rasp_app)

    # --- /redirect sub-application ---

    redirect_app = FastAPI()

    @redirect_app.get("/{route:str}/{port:int}", response_class=JSONResponse)
    async def redirect_get(route: str, port: int, request: Request):
        import urllib.request

        url = f"http://127.0.0.1:{port}/{route}"
        try:
            request_urllib = urllib.request.Request(url, method="GET", headers={"TagRoute": route})
            with urllib.request.urlopen(request_urllib, timeout=DOWNSTREAM_HTTP_TIMEOUT) as f:
                payload = {"payload": f.read()}
        except Exception as e:
            payload = {"error": repr(e)}
        return payload

    @redirect_app.post("/{route:str}/{port:int}", response_class=JSONResponse)
    async def redirect_post(route: str, port: int, request: Request):
        import urllib.request

        url = f"http://127.0.0.1:{port}/{route}"
        try:
            request_urllib = urllib.request.Request(
                url,
                method="POST",
                data=(await request.body()),
                headers={"Content-Type": "application/json", "TagRoute": route},
            )
            with urllib.request.urlopen(request_urllib, timeout=DOWNSTREAM_HTTP_TIMEOUT) as f:
                payload = {"payload": f.read()}
        except Exception as e:
            payload = {"error": repr(e)}
        return payload

    app.mount("/redirect", redirect_app)

    # --- /redirect_requests sub-application ---

    redirect_requests_app = FastAPI()

    @redirect_requests_app.get("/{route:str}/{port:int}", response_class=JSONResponse)
    async def redirect_requests_get(route: str, port: int, request: Request):
        import requests

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            with requests.Session() as s:
                response = s.get(full_url, timeout=DOWNSTREAM_HTTP_TIMEOUT, headers={"TagRoute": route})
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        return payload

    @redirect_requests_app.post("/{route:str}/{port:int}", response_class=JSONResponse)
    async def redirect_requests_post(route: str, port: int, request: Request):
        import requests

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            with requests.Session() as s:
                response = s.post(
                    full_url,
                    data=(await request.body()),
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=DOWNSTREAM_HTTP_TIMEOUT,
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        return payload

    app.mount("/redirect_requests", redirect_requests_app)

    # --- /redirect_httpx sub-application ---

    redirect_httpx_app = FastAPI()

    @redirect_httpx_app.get("/{route:str}/{port:int}", response_class=JSONResponse)
    async def redirect_httpx_get(route: str, port: int, request: Request):
        import httpx

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            with httpx.Client() as client:
                response = client.get(
                    full_url, timeout=DOWNSTREAM_HTTP_TIMEOUT, headers={"TagRoute": route}, follow_redirects=True
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        return payload

    @redirect_httpx_app.post("/{route:str}/{port:int}", response_class=JSONResponse)
    async def redirect_httpx_post(route: str, port: int, request: Request):
        import httpx

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            with httpx.Client() as client:
                response = client.post(
                    full_url,
                    content=(await request.body()),
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=DOWNSTREAM_HTTP_TIMEOUT,
                    follow_redirects=True,
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        return payload

    app.mount("/redirect_httpx", redirect_httpx_app)

    # --- /redirect_httpx_async sub-application ---

    redirect_httpx_async_app = FastAPI()

    @redirect_httpx_async_app.get("/{route:str}/{port:int}", response_class=JSONResponse)
    async def redirect_httpx_async_get(route: str, port: int, request: Request):
        import httpx

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    full_url, timeout=DOWNSTREAM_HTTP_TIMEOUT, headers={"TagRoute": route}, follow_redirects=True
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        return payload

    @redirect_httpx_async_app.post("/{route:str}/{port:int}", response_class=JSONResponse)
    async def redirect_httpx_async_post(route: str, port: int, request: Request):
        import httpx

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    full_url,
                    content=(await request.body()),
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=DOWNSTREAM_HTTP_TIMEOUT,
                    follow_redirects=True,
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        return payload

    app.mount("/redirect_httpx_async", redirect_httpx_async_app)

    # --- /login sub-application ---

    login_app = FastAPI()

    @login_app.get("/")
    async def login_user(request: Request):
        """manual instrumentation login endpoint"""
        from ddtrace.appsec import trace_utils as appsec_trace_utils

        USERS = {
            "test": {"email": "testuser@ddog.com", "password": "1234", "name": "test", "id": "social-security-id"},
            "testuuid": {
                "email": "testuseruuid@ddog.com",
                "password": "1234",
                "name": "testuuid",
                "id": "591dc126-8431-4d0f-9509-b23318d3dce4",
            },
        }

        def authenticate(username: str, password: str) -> Optional[str]:
            if username in USERS:
                if USERS[username]["password"] == password:
                    return USERS[username]["id"]
                else:
                    appsec_trace_utils.track_user_login_failure_event(
                        tracer, user_id=USERS[username]["id"], exists=True, login_events_mode="auto", login=username
                    )
                    return None
            appsec_trace_utils.track_user_login_failure_event(
                tracer, user_id=username, exists=False, login_events_mode="auto", login=username
            )
            return None

        def login(user_id: str, username: str) -> None:
            appsec_trace_utils.track_user_login_success_event(
                tracer, user_id=user_id, login_events_mode="auto", login=username
            )

        username = request.query_params.get("username", "")
        password = request.query_params.get("password", "")
        user_id = authenticate(username=username, password=password)
        if user_id is not None:
            login(user_id, username)
            return HTMLResponse("OK")
        return HTMLResponse("login failure", status_code=401)

    app.mount("/login", login_app)

    # --- /login_sdk sub-application ---

    login_sdk_app = FastAPI()

    @login_sdk_app.get("/")
    async def login_user_sdk(request: Request):
        """manual instrumentation login endpoint using SDK V2"""
        try:
            from ddtrace.appsec import track_user_sdk
        except ImportError:
            return HTMLResponse("SDK V2 not available", status_code=422)

        USERS = {
            "test": {"email": "testuser@ddog.com", "password": "1234", "name": "test", "id": "social-security-id"},
            "testuuid": {
                "email": "testuseruuid@ddog.com",
                "password": "1234",
                "name": "testuuid",
                "id": "591dc126-8431-4d0f-9509-b23318d3dce4",
            },
        }
        metadata = json.loads(request.query_params.get("metadata", "{}"))

        def authenticate(username: str, password: str) -> Optional[str]:
            if username in USERS:
                if USERS[username]["password"] == password:
                    return USERS[username]["id"]
                track_user_sdk.track_login_failure(
                    login=username, user_id=USERS[username]["id"], exists=True, metadata=metadata
                )
                return None
            track_user_sdk.track_login_failure(login=username, exists=False, metadata=metadata)
            return None

        def login(user_id: str, login: str) -> None:
            track_user_sdk.track_login_success(login=login, user_id=user_id, metadata=metadata)

        username = request.query_params.get("username", "")
        password = request.query_params.get("password", "")
        user_id = authenticate(username=username, password=password)
        if user_id is not None:
            login(user_id, username)
            return HTMLResponse("OK")
        return HTMLResponse("login failure", status_code=401)

    app.mount("/login_sdk", login_sdk_app)

    return app
