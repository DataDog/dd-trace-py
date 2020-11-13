import asyncio
import sys

import httpx
import pytest
import sqlalchemy

import ddtrace
from ddtrace import Pin
from ddtrace import config
from ddtrace.contrib.starlette import patch as starlette_patch, unpatch as starlette_unpatch
from ddtrace.contrib.sqlalchemy import patch as sql_patch, unpatch as sql_unpatch
from ddtrace.propagation import http as http_propagation


from starlette.testclient import TestClient
from tests import override_http_config, snapshot
from tests.tracer.test_tracer import get_dummy_tracer

from app import get_app


@pytest.fixture
def engine():
    sql_patch()
    engine = sqlalchemy.create_engine("sqlite:///test.db")
    yield engine
    sql_unpatch()


@pytest.fixture
def tracer(engine):
    original_tracer = ddtrace.tracer
    tracer = get_dummy_tracer()
    if sys.version_info < (3, 7):
        # enable legacy asyncio support
        from ddtrace.contrib.asyncio.provider import AsyncioContextProvider

        Pin.override(engine, tracer=tracer)
        tracer.configure(context_provider=AsyncioContextProvider())

    setattr(ddtrace, "tracer", tracer)
    starlette_patch()
    yield tracer
    setattr(ddtrace, "tracer", original_tracer)
    starlette_unpatch()


@pytest.fixture
def app(tracer, engine):
    app = get_app(engine)
    yield app


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def snapshot_app(engine):
    app = get_app(engine)
    yield app


@pytest.fixture
def snapshot_client(snapshot_app):
    with TestClient(snapshot_app) as test_client:
        yield test_client


def test_200(client, tracer):
    r = client.get("/200")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /200"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/200"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") is None


def test_200_query_string(client, tracer):
    with override_http_config("starlette", dict(trace_query_string=True)):
        r = client.get("?foo=bar")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "foo=bar"


def test_200_multi_query_string(client, tracer):
    with override_http_config("starlette", dict(trace_query_string=True)):
        r = client.get("?foo=bar&x=y")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "foo=bar&x=y"


def test_201(client, tracer):
    r = client.post("/201")

    assert r.status_code == 201
    assert r.text == "Created"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "POST /201"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "POST"
    assert request_span.get_tag("http.url") == "http://testserver/201"
    assert request_span.get_tag("http.status_code") == "201"
    assert request_span.get_tag("http.query.string") is None


def test_404(client, tracer):
    r = client.get("/404")

    assert r.status_code == 404
    assert r.text == "Not Found"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /404"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/404"
    assert request_span.get_tag("http.status_code") == "404"


def test_500error(client, tracer):
    with pytest.raises(RuntimeError):
        client.get("/500")

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /500"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/500"
    assert request_span.get_tag("http.status_code") == "500"
    assert request_span.get_tag("error.msg") == "Server error"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert 'raise RuntimeError("Server error")' in request_span.get_tag("error.stack")


def test_distributed_tracing(client, tracer):
    headers = [
        (http_propagation.HTTP_HEADER_PARENT_ID, "1234"),
        (http_propagation.HTTP_HEADER_TRACE_ID, "5678"),
    ]
    r = client.get("http://testserver/", headers=dict(headers))

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.parent_id == 1234
    assert request_span.trace_id == 5678


@pytest.mark.asyncio
async def test_multiple_requests(app, tracer):
    with override_http_config("starlette", dict(trace_query_string=True)):
        async with httpx.AsyncClient(app=app) as client:
            responses = await asyncio.gather(
                client.get("http://testserver/", params={"sleep": True}),
                client.get("http://testserver/", params={"sleep": True}),
            )

    assert len(responses) == 2
    assert [r.status_code for r in responses] == [200] * 2
    assert [r.text for r in responses] == ["Success"] * 2

    spans = tracer.writer.pop_traces()
    assert len(spans) == 2
    assert len(spans[0]) == 1
    assert len(spans[1]) == 1

    r1_span = spans[0][0]
    assert r1_span.service == "starlette"
    assert r1_span.name == "starlette.request"
    assert r1_span.resource == "GET /"
    assert r1_span.get_tag("http.method") == "GET"
    assert r1_span.get_tag("http.url") == "http://testserver/"
    assert r1_span.get_tag("http.query.string") == "sleep=true"

    r2_span = spans[0][0]
    assert r2_span.service == "starlette"
    assert r2_span.name == "starlette.request"
    assert r2_span.resource == "GET /"
    assert r2_span.get_tag("http.method") == "GET"
    assert r2_span.get_tag("http.url") == "http://testserver/"
    assert r2_span.get_tag("http.query.string") == "sleep=true"


def test_streaming_response(client, tracer):
    r = client.get("/stream")

    assert r.status_code == 200

    assert r.text.endswith("streaming")

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /stream"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/stream"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"


def test_file_response(client, tracer):
    r = client.get("/file")

    assert r.status_code == 200
    assert r.text == "Datadog says hello!"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /file"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/file"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"


def test_invalid_path_param(client, tracer):
    r = client.get("/users/test")

    assert r.status_code == 404
    assert r.text == "Not Found"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/test"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/test"
    assert request_span.get_tag("http.status_code") == "404"


def test_path_param_aggregate(client, tracer):
    r = client.get("/users/1")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/{userid:int}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/1"
    assert request_span.get_tag("http.status_code") == "200"


def test_mid_path_param_aggregate(client, tracer):
    r = client.get("/users/1/info")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/{userid:int}/info"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/1/info"
    assert request_span.get_tag("http.status_code") == "200"


def test_multi_path_param_aggregate(client, tracer):
    r = client.get("/users/1/name")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/{userid:int}/{attribute:str}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/1/name"
    assert request_span.get_tag("http.status_code") == "200"


def test_path_param_no_aggregate(client, tracer):
    config.starlette["aggregate_resources"] = False
    r = client.get("/users/1")

    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/1"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/1"
    assert request_span.get_tag("http.status_code") == "200"
    config.starlette["aggregate_resources"] = True


def test_table_query(client, tracer):
    r = client.post("/notes", json={"id": 1, "text": "test", "completed": 1})
    assert r.status_code == 200
    assert r.text == "Success"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2

    starlette_span = spans[0][0]
    assert starlette_span.service == "starlette"
    assert starlette_span.name == "starlette.request"
    assert starlette_span.resource == "POST /notes"
    assert starlette_span.error == 0
    assert starlette_span.get_tag("http.method") == "POST"
    assert starlette_span.get_tag("http.url") == "http://testserver/notes"
    assert starlette_span.get_tag("http.status_code") == "200"

    sql_span = spans[0][1]
    assert sql_span.service == "sqlite"
    assert sql_span.name == "sqlite.query"
    assert sql_span.resource == "INSERT INTO notes (id, text, completed) VALUES (?, ?, ?)"
    assert sql_span.error == 0
    assert sql_span.get_tag("sql.db") == "test.db"

    r = client.get("/notes")

    assert r.status_code == 200
    assert r.text == "[{'id': 1, 'text': 'test', 'completed': 1}]"

    spans = tracer.writer.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    starlette_span = spans[0][0]
    assert starlette_span.service == "starlette"
    assert starlette_span.name == "starlette.request"
    assert starlette_span.resource == "GET /notes"
    assert starlette_span.error == 0
    assert starlette_span.get_tag("http.method") == "GET"
    assert starlette_span.get_tag("http.url") == "http://testserver/notes"
    assert starlette_span.get_tag("http.status_code") == "200"

    sql_span = spans[0][1]
    assert sql_span.service == "sqlite"
    assert sql_span.name == "sqlite.query"
    assert sql_span.resource == "SELECT * FROM NOTES"
    assert sql_span.error == 0
    assert sql_span.get_tag("sql.db") == "test.db"


@snapshot()
def test_table_query_snapshot(snapshot_client):
    r_post = snapshot_client.post("/notes", json={"id": 1, "text": "test", "completed": 1})

    assert r_post.status_code == 200
    assert r_post.text == "Success"

    r_get = snapshot_client.get("/notes")
    assert r_get.status_code == 200
    assert r_get.text == "[{'id': 1, 'text': 'test', 'completed': 1}]"
