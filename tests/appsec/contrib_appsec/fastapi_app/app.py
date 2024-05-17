import asyncio
from typing import Optional

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ddtrace import tracer
import ddtrace.constants


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

        ddtrace.Pin.override(app, service=service_name, tracer=ddtrace.tracer)
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
        elif endpoint == "shell":
            res = ["shell endpoint"]
            for param in query_params:
                if param.startswith("cmd"):
                    cmd = query_params[param]
                    try:
                        import subprocess

                        with subprocess.Popen(cmd, stdout=subprocess.PIPE) as f:
                            res.append(f"cmd stdout: {f.stdout.read()}")
                    except Exception as e:
                        res.append(f"Error: {e}")
            tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
            return HTMLResponse("<\\br>\n".join(res))
        tracer.current_span()._local_root.set_tag("rasp.request.done", endpoint)
        return HTMLResponse(f"Unknown endpoint: {endpoint}")

    return app
