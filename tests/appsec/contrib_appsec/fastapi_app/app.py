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
    async def read_homepage():  # noqa: B008
        return HTMLResponse("ok ASM", 200)

    @app.get("/asm/{param_int:int}/{param_str:str}/")
    @app.post("/asm/{param_int:int}/{param_str:str}/")
    async def multi_view(param_int: int, param_str: str, request: Request):  # noqa: B008
        query_params = request.query_params
        body = {
            "path_params": {"param_int": param_int, "param_str": param_str},
            "query_params": query_params,
            "headers": dict(request.headers),
            "cookies": dict(request.cookies),
            "body": request.body.decode("utf-8"),
            "method": request.method,
        }
        status = int(query_params.get("status", "200"))
        return JSONResponse(body, status_code=status)

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

    return app
