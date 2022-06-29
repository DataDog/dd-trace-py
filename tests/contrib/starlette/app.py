import asyncio
from tempfile import NamedTemporaryFile
import time

import databases
import sqlalchemy
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.responses import FileResponse
from starlette.responses import JSONResponse
from starlette.responses import PlainTextResponse
from starlette.responses import Response
from starlette.responses import StreamingResponse
from starlette.routing import Mount
from starlette.routing import Route


def create_test_database(engine):
    engine.execute("DROP TABLE IF EXISTS notes;")
    metadata = sqlalchemy.MetaData()
    sqlalchemy.Table(
        "notes",
        metadata,
        sqlalchemy.Column("id", sqlalchemy.Integer, primary_key=True),
        sqlalchemy.Column("text", sqlalchemy.String),
        sqlalchemy.Column("completed", sqlalchemy.Boolean),
    )
    metadata.create_all(engine)


async def homepage(request):
    if "sleep" in request.query_params and request.query_params["sleep"]:
        time.sleep(3)
    response = "Success"
    return PlainTextResponse(response)


async def success(request):
    response = "Success"
    return PlainTextResponse(response)


async def create(request):
    response = "Created"
    return Response(response, status_code=201, headers=None, media_type=None)


async def error(request):
    """
    An example error. Switch the `debug` setting to see either tracebacks or 500 pages.
    """
    raise RuntimeError("Server error")


async def server_error(request, exc):
    """
    Return an HTTP 500 page.
    """
    response = "Server error"
    return PlainTextResponse(response)


def stream_response():
    yield b"streaming"


async def stream(request):
    return StreamingResponse(stream_response())


async def file(request):
    with NamedTemporaryFile(delete=False) as fp:
        fp.write(b"Datadog says hello!")
        fp.flush()
        return FileResponse(fp.name)


def get_app(engine):
    create_test_database(engine)

    DATABASE_URL = "sqlite:///test.db"
    database = databases.Database(DATABASE_URL)
    metadata = sqlalchemy.MetaData(bind=engine)
    metadata.reflect()

    notes_table = metadata.tables["notes"]

    async def list_notes(request):
        if not engine:
            raise RuntimeError("Server error")
        query = "SELECT * FROM NOTES"
        with engine.connect() as connection:
            result = connection.execute(query)
            d, a = {}, []
            for rowproxy in result:
                for column, value in rowproxy.items():
                    d = {**d, **{column: value}}
                a.append(d)
        response = str(a)
        return PlainTextResponse(response)

    async def add_note(request):
        if not engine:
            raise RuntimeError("Server error")
        request_json = await request.json()
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(notes_table.insert(), request_json)
        response = "Success"
        return PlainTextResponse(response)

    async def custom_task():
        await asyncio.sleep(1)

    async def background_task(request):
        """An example endpoint that just adds to background tasks"""
        jsonmsg = {"result": "Background task added"}
        return JSONResponse(jsonmsg, background=BackgroundTask(custom_task))

    routes = [
        Route("/", endpoint=homepage, name="homepage", methods=["GET"]),
        Route("/200", endpoint=success, name="200", methods=["GET"]),
        Route("/201", endpoint=create, name="201", methods=["POST"]),
        Route("/500", endpoint=error, name="500", methods=["GET"]),
        Route("/stream", endpoint=stream, name="stream", methods=["GET"]),
        Route("/file", endpoint=file, name="file", methods=["GET"]),
        Route("/users/{userid:int}", endpoint=success, name="path_params", methods=["GET"]),
        Route("/users/{userid:int}/info", endpoint=success, name="multi_path_params", methods=["GET"]),
        Route("/users/{userid:int}/{attribute:str}", endpoint=success, name="multi_path_params", methods=["GET"]),
        Route("/notes", endpoint=list_notes, methods=["GET"]),
        Route("/notes", endpoint=add_note, methods=["POST"]),
        Mount("/sub-app", Starlette(routes=[Route("/hello/{name}", endpoint=success, name="200", methods=["GET"])])),
        Route("/backgroundtask", endpoint=background_task, name="200", methods=["GET"]),
    ]

    app = Starlette(routes=routes, on_startup=[database.connect], on_shutdown=[database.disconnect])
    return app
