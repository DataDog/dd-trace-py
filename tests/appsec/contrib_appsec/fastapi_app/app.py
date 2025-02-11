import asyncio
import os
import sqlite3
import subprocess
from typing import Optional

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import ddtrace.constants
from ddtrace.trace import tracer


fake_secret_token = "DataDog"

fake_db = {
    "foo": {"id": "foo", "name": "Foo", "description": "This item's description is foo."},
    "bar": {"id": "bar", "name": "Bar", "description": "The bartenders"},
    "testUserID": {"userid": "testUserID", "name": "Test User"},
}


class Item(BaseModel):
    id: str
    name: str
    description: Optional[str] = None


class User(BaseModel):
    userid: int
    name: str


def get_app():
    app = FastAPI()

    @app.get("/")
    @app.post("/")
    @app.options("/")
    async def read_homepage():  # noqa: B008
        return HTMLResponse("ok ASM", 200)

    @app.get("/asm/{param_int:int}/{param_str:str}/")
    @app.post("/asm/{param_int:int}/{param_str:str}/")
    @app.get("/asm/{param_int:int}/{param_str:str}")
    @app.post("/asm/{param_int:int}/{param_str:str}")
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

    @app.get("/asm/")
    @app.post("/asm/")
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

    @app.get("/new_service/{service_name:str}/")
    @app.post("/new_service/{service_name:str}/")
    @app.get("/new_service/{service_name:str}")
    @app.post("/new_service/{service_name:str}")
    async def new_service(service_name: str, request: Request):  # noqa: B008
        import ddtrace

        ddtrace.trace.Pin._override(app, service=service_name, tracer=ddtrace.tracer)
        return HTMLResponse(service_name, 200)

    async def slow_numbers(minimum, maximum):
        for number in range(minimum, maximum):
            yield "%d" % number
            await asyncio.sleep(0.25)

    @app.get("/stream/")
    async def stream():
        return StreamingResponse(slow_numbers(0, 10), media_type="text/html")

    @app.get("/rasp/{endpoint:str}/")
    @app.post("/rasp/{endpoint:str}/")
    @app.options("/rasp/{endpoint:str}/")
    async def rasp(endpoint: str, request: Request):
        DB = sqlite3.connect(":memory:")
        DB.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)")
        DB.execute("INSERT INTO users (id, name) VALUES ('1_secret_id', 'Alice')")
        DB.execute("INSERT INTO users (id, name) VALUES ('2_secret_id', 'Bob')")
        DB.execute("INSERT INTO users (id, name) VALUES ('3_secret_id', 'Christophe')")
        query_params = request.query_params
        if endpoint == "lfi":
            res = ["lfi endpoint"]
            for param in query_params:
                if param.startswith("filename"):
                    filename = query_params[param]
                try:
                    with open(filename, "rb") as f:
                        res.append(f"File: {f.read()}")
                except Exception as e:
                    res.append(f"Error: {e}")
            tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\br>\n".join(res))
        elif endpoint == "ssrf":
            res = ["ssrf endpoint"]
            for param in query_params:
                if param.startswith("url"):
                    urlname = query_params[param]
                    if not urlname.startswith("http"):
                        urlname = f"http://{urlname}"
                try:
                    if param.startswith("url_urlopen_request"):
                        import urllib.request

                        request = urllib.request.Request(urlname)
                        with urllib.request.urlopen(request, timeout=0.15) as f:
                            res.append(f"Url: {f.read()}")
                    elif param.startswith("url_urlopen_string"):
                        import urllib.request

                        with urllib.request.urlopen(urlname, timeout=0.15) as f:
                            res.append(f"Url: {f.read()}")
                    elif param.startswith("url_requests"):
                        import requests

                        r = requests.get(urlname, timeout=0.15)
                        res.append(f"Url: {r.text}")
                except Exception as e:
                    res.append(f"Error: {e}")
            tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
        elif endpoint == "sql_injection":
            res = ["sql_injection endpoint"]
            for param in query_params:
                if param.startswith("user_id"):
                    user_id = query_params[param]
                try:
                    if param.startswith("user_id"):
                        cursor = DB.execute(f"SELECT * FROM users WHERE id = {user_id}")
                        res.append(f"Url: {list(cursor)}")
                except Exception as e:
                    res.append(f"Error: {e}")
            tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
        elif endpoint == "shell_injection":
            res = ["shell_injection endpoint"]
            for param in query_params:
                if param.startswith("cmd"):
                    cmd = query_params[param]
                    try:
                        if param.startswith("cmdsys"):
                            res.append(f'cmd stdout: {os.system(f"ls {cmd}")}')
                        else:
                            res.append(f'cmd stdout: {subprocess.run(f"ls {cmd}", shell=True)}')
                    except Exception as e:
                        res.append(f"Error: {e}")
            tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
        elif endpoint == "command_injection":
            res = ["command_injection endpoint"]
            for param in query_params:
                if param.startswith("cmda"):
                    cmd = query_params[param]
                    try:
                        res.append(f'cmd stdout: {subprocess.run([cmd, "-c", "3", "localhost"])}')
                    except Exception as e:
                        res.append(f"Error: {e}")
                elif param.startswith("cmds"):
                    cmd = query_params[param]
                    try:
                        res.append(f"cmd stdout: {subprocess.run(cmd)}")
                    except Exception as e:
                        res.append(f"Error: {e}")
            tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
        tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
        return HTMLResponse(f"Unknown endpoint: {endpoint}")

    @app.get("/login/")
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
            """authenticate user"""
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
            """login user"""
            appsec_trace_utils.track_user_login_success_event(
                tracer, user_id=user_id, login_events_mode="auto", login=username
            )

        username = request.query_params.get("username")
        password = request.query_params.get("password")
        user_id = authenticate(username=username, password=password)
        if user_id is not None:
            login(user_id, username)
            return HTMLResponse("OK")
        return HTMLResponse("login failure", status_code=401)

    return app
