import asyncio
import json
import os
import sqlite3
import subprocess
from typing import AsyncGenerator
from typing import Optional

import tornado.ioloop
import tornado.web

from ddtrace._trace.pin import Pin
import ddtrace.constants
from ddtrace.trace import tracer


fake_secret_token = "DataDog"

fake_db = {
    "foo": {"id": "foo", "name": "Foo", "description": "This item's description is foo."},
    "bar": {"id": "bar", "name": "Bar", "description": "The bartenders"},
    "testUserID": {"userid": "testUserID", "name": "Test User"},
}

def _create_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT)")
    db.execute("INSERT INTO users (id, name) VALUES ('1_secret_id', 'Alice')")
    db.execute("INSERT INTO users (id, name) VALUES ('2_secret_id', 'Bob')")
    db.execute("INSERT INTO users (id, name) VALUES ('3_secret_id', 'Christophe')")
    return db


async def get_db() -> AsyncGenerator[sqlite3.Connection, None]:
    db = _create_db()
    try:
        yield db
    finally:
        db.close()


async def slow_numbers(minimum, maximum):
    for number in range(minimum, maximum):
        yield "%d" % number
        await asyncio.sleep(0.0625)


def _set_rasp_done(endpoint: str) -> None:
    span = tracer.current_span()
    if span is not None and getattr(span, "_service_entry_span", None) is not None:
        span._service_entry_span.set_tag("rasp.request.done", endpoint)


class BaseHandler(tornado.web.RequestHandler):
    def prepare(self) -> None:
        """Middleware to test BlockingException nesting in ExceptionGroups (or BaseExceptionGroups)

        With middlewares, the BlockingException can become nested multiple levels deep inside
        an ExceptionGroup (or BaseExceptionGroup). The nesting depends the version of FastAPI
        and AnyIO used, as well as the version of python.
        By adding this empty middleware, we ensure that the BlockingException is caught
        no matter how deep the ExceptionGroup is nested or else the contrib tests fail.
        """
        self._passthrough_middleware()
        self._rename_service()

    def _passthrough_middleware(self) -> None:
        return None

    def _rename_service(self) -> None:
        if self.request.headers.get("x-rename-service", "false").lower() == "true":
            service_name = "sub-service"
            root_span = tracer.current_root_span()
            if root_span is not None:
                root_span.service = service_name
                root_span.set_tag("scope", service_name)

    def _query_params(self) -> dict:
        return {k: self.get_query_argument(k) for k in self.request.query_arguments}

    def _cookies(self) -> dict:
        return {k: v.value for k, v in self.request.cookies.items()}

    def _headers(self) -> dict:
        return dict(self.request.headers)

    def _set_manual_priority(self, priority: Optional[str]) -> None:
        if priority in ("keep", "drop"):
            span = tracer.current_span()
            if span is not None:
                span.set_tag(
                    ddtrace.constants.MANUAL_KEEP_KEY if priority == "keep" else ddtrace.constants.MANUAL_DROP_KEY
                )

    def _write_json(self, payload: dict, status: int = 200, headers: Optional[dict] = None) -> None:
        self.set_status(status)
        if headers:
            for key, value in headers.items():
                self.set_header(key, value)
        self.set_header("Content-Type", "application/json")
        self.write(json.dumps(payload))


class HomeHandler(BaseHandler):
    def get(self) -> None:
        self.set_header("Content-Type", "text/html")
        self.write("ok ASM")

    def post(self) -> None:
        self.set_header("Content-Type", "text/html")
        self.write("ok ASM")

    def options(self) -> None:
        self.set_header("Content-Type", "text/html")
        self.write("ok ASM")


class AsmHandler(BaseHandler):
    async def _handle(self, param_int: int, param_str: str) -> None:
        query_params = self._query_params()
        body = {
            "path_params": {"param_int": param_int, "param_str": param_str},
            "query_params": query_params,
            "cookies": self._cookies(),
            "body": self.request.body.decode("utf-8"),
            "method": self.request.method,
        }
        status = int(query_params.get("status", "200"))
        headers_query = query_params.get("headers", "").split(",")
        priority = query_params.get("priority", None)
        self._set_manual_priority(priority)
        response_headers = {}
        for header in headers_query:
            vk = header.split("=")
            if len(vk) == 2:
                response_headers[vk[0]] = vk[1]
        self._write_json(body, status=status, headers=response_headers)

    async def get(self, param_int: str, param_str: str) -> None:
        await self._handle(int(param_int), param_str)

    async def post(self, param_int: str, param_str: str) -> None:
        await self._handle(int(param_int), param_str)


class AsmNoParamHandler(BaseHandler):
    async def _handle(self) -> None:
        query_params = self._query_params()
        body = {
            "path_params": {"param_int": 0, "param_str": ""},
            "query_params": query_params,
            "headers": dict(self._headers),
            "cookies": self._cookies(),
            "body": self.request.body.decode("utf-8"),
            "method": self.request.method,
        }
        status = int(query_params.get("status", "200"))
        headers_query = query_params.get("headers", "").split(",")
        priority = query_params.get("priority", None)
        self._set_manual_priority(priority)
        response_headers = {}
        for header in headers_query:
            vk = header.split("=")
            if len(vk) == 2:
                response_headers[vk[0]] = vk[1]
        self._write_json(body, status=status, headers=response_headers)

    async def get(self) -> None:
        await self._handle()

    async def post(self) -> None:
        await self._handle()


class NewServiceHandler(BaseHandler):
    def initialize(self, app: tornado.web.Application) -> None:
        self._app = app

    async def _handle(self, service_name: str) -> None:
        import ddtrace

        Pin._override(self._app, service=service_name, tracer=ddtrace.tracer)
        self.set_header("Content-Type", "text/html")
        self.write(service_name)

    async def get(self, service_name: str) -> None:
        await self._handle(service_name)

    async def post(self, service_name: str) -> None:
        await self._handle(service_name)


class StreamHandler(BaseHandler):
    async def get(self) -> None:
        self.set_header("Content-Type", "text/html")
        async for chunk in slow_numbers(0, 10):
            self.write(chunk)
            await self.flush()


class RaspHandler(BaseHandler):
    async def _handle(self, endpoint: str) -> None:
        query_params = self._query_params()
        async for db in get_db():
            if endpoint == "lfi":
                res = ["lfi endpoint"]
                filename = ""
                for param in query_params:
                    if param.startswith("filename"):
                        filename = query_params[param]
                    try:
                        with open(filename, "rb") as f:
                            res.append(f"File: {f.read()}")
                    except Exception as e:
                        res.append(f"Error: {e}")
                _set_rasp_done(endpoint)
                self.set_header("Content-Type", "text/html")
                self.write("<\\br>\n".join(res))
                return
            if endpoint == "ssrf":
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
                _set_rasp_done(endpoint)
                self.set_header("Content-Type", "text/html")
                self.write("<\\br>\n".join(res))
                return
            if endpoint == "sql_injection":
                res = ["sql_injection endpoint"]
                user_id = ""
                for param in query_params:
                    if param.startswith("user_id"):
                        user_id = query_params[param]
                    try:
                        if param.startswith("user_id"):
                            cursor = db.execute(f"SELECT * FROM users WHERE id = {user_id}")
                            res.append(f"Url: {list(cursor)}")
                    except Exception as e:
                        res.append(f"Error: {e}")
                _set_rasp_done(endpoint)
                self.set_header("Content-Type", "text/html")
                self.write("<\\br>\n".join(res))
                return
            if endpoint == "shell_injection":
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
                _set_rasp_done(endpoint)
                self.set_header("Content-Type", "text/html")
                self.write("<\\br>\n".join(res))
                return
            if endpoint == "command_injection":
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
                _set_rasp_done(endpoint)
                self.set_header("Content-Type", "text/html")
                self.write("<\\br>\n".join(res))
                return
            _set_rasp_done(endpoint)
            self.set_header("Content-Type", "text/html")
            self.write(f"Unknown endpoint: {endpoint}")

    async def get(self, endpoint: str) -> None:
        await self._handle(endpoint)

    async def post(self, endpoint: str) -> None:
        await self._handle(endpoint)

    async def options(self, endpoint: str) -> None:
        await self._handle(endpoint)


class RedirectHandler(BaseHandler):
    async def get(self, route: str, port: str) -> None:
        import urllib.request

        url = f"http://127.0.0.1:{port}/{route}"
        try:
            request_urllib = urllib.request.Request(url, method="GET", headers={"TagRoute": route})
            with urllib.request.urlopen(request_urllib, timeout=0.5) as f:
                payload = {"payload": f.read().decode("utf-8", errors="replace")}
        except Exception as e:
            payload = {"error": repr(e)}
        self._write_json(payload)

    async def post(self, route: str, port: str) -> None:
        import urllib.request

        url = f"http://127.0.0.1:{port}/{route}"
        try:
            request_urllib = urllib.request.Request(
                url,
                method="POST",
                data=self.request.body,
                headers={
                    "Content-Type": "application/json",
                    "TagRoute": route,
                },
            )
            with urllib.request.urlopen(request_urllib, timeout=0.5) as f:
                payload = {"payload": f.read().decode("utf-8", errors="replace")}
        except Exception as e:
            payload = {"error": repr(e)}
        self._write_json(payload)


class RedirectRequestsHandler(BaseHandler):
    async def get(self, route: str, port: str) -> None:
        import requests

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            with requests.Session() as s:
                response = s.get(full_url, timeout=0.5, headers={"TagRoute": route})
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        self._write_json(payload)

    async def post(self, route: str, port: str) -> None:
        import requests

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            with requests.Session() as s:
                response = s.post(
                    full_url,
                    data=self.request.body,
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=0.5,
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        self._write_json(payload)


class RedirectHttpxHandler(BaseHandler):
    async def get(self, route: str, port: str) -> None:
        import httpx

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            with httpx.Client() as client:
                response = client.get(full_url, timeout=0.5, headers={"TagRoute": route}, follow_redirects=True)
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        self._write_json(payload)

    async def post(self, route: str, port: str) -> None:
        import httpx

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            with httpx.Client() as client:
                response = client.post(
                    full_url,
                    content=self.request.body,
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=0.5,
                    follow_redirects=True,
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        self._write_json(payload)


class RedirectHttpxAsyncHandler(BaseHandler):
    async def get(self, route: str, port: str) -> None:
        import httpx

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    full_url, timeout=0.5, headers={"TagRoute": route}, follow_redirects=True
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        self._write_json(payload)

    async def post(self, route: str, port: str) -> None:
        import httpx

        full_url = f"http://127.0.0.1:{port}/{route}"
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    full_url,
                    content=self.request.body,
                    headers={"Content-Type": "application/json", "TagRoute": route},
                    timeout=0.5,
                    follow_redirects=True,
                )
                payload = {"payload": response.text}
        except Exception as e:
            payload = {"error": repr(e)}
        self._write_json(payload)


class LoginHandler(BaseHandler):
    async def get(self) -> None:
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

        username = self.get_query_argument("username", "")
        password = self.get_query_argument("password", "")
        user_id = authenticate(username=username, password=password)
        if user_id is not None:
            login(user_id, username)
            self.set_header("Content-Type", "text/html")
            self.write("OK")
            return
        self.set_header("Content-Type", "text/html")
        self.set_status(401)
        self.write("login failure")


class LoginSdkHandler(BaseHandler):
    async def get(self) -> None:
        """manual instrumentation login endpoint using SDK V2"""
        try:
            from ddtrace.appsec import track_user_sdk
        except ImportError:
            self.set_header("Content-Type", "text/html")
            self.set_status(422)
            self.write("SDK V2 not available")
            return

        USERS = {
            "test": {"email": "testuser@ddog.com", "password": "1234", "name": "test", "id": "social-security-id"},
            "testuuid": {
                "email": "testuseruuid@ddog.com",
                "password": "1234",
                "name": "testuuid",
                "id": "591dc126-8431-4d0f-9509-b23318d3dce4",
            },
        }
        metadata = json.loads(self.get_query_argument("metadata", "{}"))

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

        def login(user_id: str, login: str) -> None:
            """login user"""
            track_user_sdk.track_login_success(login=login, user_id=user_id, metadata=metadata)

        username = self.get_query_argument("username", "")
        password = self.get_query_argument("password", "")
        user_id = authenticate(username=username, password=password)
        if user_id is not None:
            login(user_id, username)
            self.set_header("Content-Type", "text/html")
            self.write("OK")
            return
        self.set_header("Content-Type", "text/html")
        self.set_status(401)
        self.write("login failure")


def get_app() -> tornado.web.Application:
    app = tornado.web.Application(
        [
            (r"/", HomeHandler),
            (r"/asm/(?P<param_int>\d+)/(?P<param_str>[^/]+)/?", AsmHandler),
            (r"/asm/?", AsmNoParamHandler),
            (r"/new_service/(?P<service_name>[^/]+)/?", NewServiceHandler, {"app": None}),
            (r"/stream/?", StreamHandler),
            (r"/rasp/(?P<endpoint>[^/]+)/?", RaspHandler),
            (r"/redirect/(?P<route>[^/]+)/(?P<port>\d+)/?", RedirectHandler),
            (r"/redirect_requests/(?P<route>[^/]+)/(?P<port>\d+)/?", RedirectRequestsHandler),
            (r"/redirect_httpx/(?P<route>[^/]+)/(?P<port>\d+)/?", RedirectHttpxHandler),
            (r"/redirect_httpx_async/(?P<route>[^/]+)/(?P<port>\d+)/?", RedirectHttpxAsyncHandler),
            (r"/login/?", LoginHandler),
            (r"/login_sdk/?", LoginSdkHandler),
        ]
    )
    for spec in app.wildcard_router.rules:
        if isinstance(spec.target, type) and issubclass(spec.target, NewServiceHandler) and hasattr(spec, "kwargs"):
            spec.kwargs["app"] = app
    return app
