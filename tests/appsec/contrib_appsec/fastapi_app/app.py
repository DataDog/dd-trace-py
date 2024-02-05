from typing import Optional

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from pydantic import BaseModel


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
        return JSONResponse(body, status_code=status)

    @app.get("/new_service/{service_name:str}/")
    @app.post("/new_service/{service_name:str}/")
    @app.get("/new_service/{service_name:str}")
    @app.post("/new_service/{service_name:str}")
    async def new_service(service_name: str, request: Request):  # noqa: B008
        import ddtrace

        ddtrace.Pin.override(app, service=service_name, tracer=ddtrace.tracer)
        return HTMLResponse(service_name, 200)

    return app
