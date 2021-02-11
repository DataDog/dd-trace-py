from tempfile import NamedTemporaryFile
import time
from typing import Optional

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Header
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
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
    async def read_homepage(sleep: str = Header(...)):
        if sleep == "True":
            time.sleep(2)
            return {"Homepage Read": "Sleep"}
        return {"Homepage Read": "Success"}

    @app.get("/items/{item_id}", response_model=Item)
    async def read_item(item_id: str, x_token: str = Header(...)):
        if x_token != fake_secret_token:
            raise HTTPException(status_code=401, detail="Invalid X-Token header")
        if item_id not in fake_db:
            raise HTTPException(status_code=404, detail="Item not found")
        return fake_db[item_id]

    @app.post("/items/", response_model=Item)
    async def create_item(item: Item, x_token: str = Header(...)):
        if x_token != fake_secret_token:
            raise HTTPException(status_code=401, detail="Invalid X-Token header")
        if item.id in fake_db:
            raise HTTPException(status_code=400, detail="Item already exists")
        fake_db[item.id] = item
        return item

    @app.get("/users/{userid:str}")
    async def get_user(userid: str, x_token: str = Header(...)):
        if x_token != fake_secret_token:
            raise HTTPException(status_code=401, detail="Invalid X-Token header")
        if userid not in fake_db:
            raise HTTPException(status_code=404, detail="User not found")
        return fake_db[userid]

    @app.get("/users/{userid:str}/info")
    async def get_user_info(userid: str, x_token: str = Header(...)):
        if x_token != fake_secret_token:
            raise HTTPException(status_code=401, detail="Invalid X-Token header")
        if userid not in fake_db:
            raise HTTPException(status_code=404, detail="User not found")
        return {"User Info": "Here"}

    @app.get("/users/{userid:str}/{attribute:str}")
    async def get_user_attribute(userid: str, attribute: str, x_token: str = Header(...)):
        if x_token != fake_secret_token:
            raise HTTPException(status_code=401, detail="Invalid X-Token header")
        if userid not in fake_db:
            raise HTTPException(status_code=404, detail="User not found")
        return {"User Attribute": fake_db[userid].get(attribute, "Fake Attribute")}

    @app.get("/500")
    async def error():
        """
        An example error. Switch the `debug` setting to see either tracebacks or 500 pages.
        """
        raise RuntimeError("Server error")

    @app.get("/stream")
    async def stream():
        def stream_response():
            yield b"streaming"

        return StreamingResponse(stream_response())

    @app.get("/file")
    async def file():
        with NamedTemporaryFile(delete=False) as fp:
            fp.write(b"Datadog says hello!")
            fp.flush()
            return FileResponse(fp.name)

    return app
