import asyncio
import sys

from fastapi.testclient import TestClient
import httpx
import pytest

import ddtrace
from ddtrace.contrib.fastapi import patch as fastapi_patch
from ddtrace.contrib.fastapi import unpatch as fastapi_unpatch
from ddtrace.propagation import http as http_propagation
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import override_config
from tests.utils import override_http_config
from tests.utils import snapshot

from . import app


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = DummyTracer()
    if sys.version_info < (3, 7):
        # enable legacy asyncio support
        from ddtrace.contrib.asyncio.provider import AsyncioContextProvider

        tracer.configure(context_provider=AsyncioContextProvider())

    setattr(ddtrace, "tracer", tracer)
    fastapi_patch()
    yield tracer
    setattr(ddtrace, "tracer", original_tracer)
    fastapi_unpatch()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


@pytest.fixture
def application(tracer):
    application = app.get_app()
    yield application


@pytest.fixture
def client(tracer):
    with TestClient(app.get_app()) as test_client:
        yield test_client


@pytest.fixture
def snapshot_app():
    fastapi_patch()
    application = app.get_app()
    yield application
    fastapi_unpatch()


@pytest.fixture
def snapshot_client(snapshot_app):
    with TestClient(snapshot_app) as test_client:
        yield test_client


def test_read_homepage(client, tracer, test_spans):
    response = client.get("/", headers={"sleep": "False"})
    assert response.status_code == 200
    assert response.json() == {"Homepage Read": "Success"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") is None


def test_read_item_success(client, tracer, test_spans):
    response = client.get("/items/foo", headers={"X-Token": "DataDog"})
    assert response.status_code == 200
    assert response.json() == {"id": "foo", "name": "Foo", "description": "This item's description is foo."}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/foo"
    assert request_span.get_tag("http.status_code") == "200"


def test_read_item_bad_token(client, tracer, test_spans):
    response = client.get("/items/bar", headers={"X-Token": "DataDoge"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid X-Token header"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/bar"
    assert request_span.get_tag("http.status_code") == "401"


def test_read_item_nonexistent_item(client, tracer, test_spans):
    response = client.get("/items/foobar", headers={"X-Token": "DataDog"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Item not found"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/foobar"
    assert request_span.get_tag("http.status_code") == "404"


def test_read_item_query_string(client, tracer, test_spans):
    with override_http_config("fastapi", dict(trace_query_string=True)):
        response = client.get("/items/foo?q=query", headers={"X-Token": "DataDog"})

    assert response.status_code == 200
    assert response.json() == {"id": "foo", "name": "Foo", "description": "This item's description is foo."}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/foo"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "q=query"


def test_200_multi_query_string(client, tracer, test_spans):
    with override_http_config("fastapi", dict(trace_query_string=True)):
        r = client.get("/items/foo?name=Foo&q=query", headers={"X-Token": "DataDog"})

    assert r.status_code == 200
    assert r.json() == {"id": "foo", "name": "Foo", "description": "This item's description is foo."}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/foo"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "name=Foo&q=query"


def test_create_item_success(client, tracer, test_spans):
    response = client.post(
        "/items/",
        headers={"X-Token": "DataDog"},
        json={"id": "foobar", "name": "Foo Bar", "description": "The Foo Bartenders"},
    )
    assert response.status_code == 200
    assert response.json() == {"id": "foobar", "name": "Foo Bar", "description": "The Foo Bartenders"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]

    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "POST /items/"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "POST"
    assert request_span.get_tag("http.url") == "http://testserver/items/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") is None


def test_create_item_bad_token(client, tracer, test_spans):
    response = client.post(
        "/items/",
        headers={"X-Token": "DataDoged"},
        json={"id": "foobar", "name": "Foo Bar", "description": "The Foo Bartenders"},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid X-Token header"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]

    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "POST /items/"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "POST"
    assert request_span.get_tag("http.url") == "http://testserver/items/"
    assert request_span.get_tag("http.status_code") == "401"
    assert request_span.get_tag("http.query.string") is None


def test_create_item_duplicate_item(client, tracer, test_spans):
    response = client.post(
        "/items/",
        headers={"X-Token": "DataDog"},
        json={"id": "foo", "name": "Foo", "description": "Duplicate Foo Item"},
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Item already exists"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]

    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "POST /items/"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "POST"
    assert request_span.get_tag("http.url") == "http://testserver/items/"
    assert request_span.get_tag("http.status_code") == "400"
    assert request_span.get_tag("http.query.string") is None


def test_invalid_path(client, tracer, test_spans):
    response = client.get("/invalid_path")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /invalid_path"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/invalid_path"
    assert request_span.get_tag("http.status_code") == "404"


def test_500_error_raised(client, tracer, test_spans):
    with pytest.raises(RuntimeError):
        client.get("/500", headers={"X-Token": "DataDog"})
    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1

    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /500"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/500"
    assert request_span.get_tag("http.status_code") == "500"
    assert request_span.get_tag("error.msg") == "Server error"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert 'raise RuntimeError("Server error")' in request_span.get_tag("error.stack")


def test_streaming_response(client, tracer, test_spans):
    response = client.get("/stream")
    assert response.status_code == 200
    assert response.text.endswith("streaming")

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /stream"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/stream"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"


def test_file_response(client, tracer, test_spans):
    response = client.get("/file", headers={"X-Token": "DataDog"})
    assert response.status_code == 200
    assert response.text == "Datadog says hello!"

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /file"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/file"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"


def test_path_param_aggregate(client, tracer, test_spans):
    response = client.get("/users/testUserID", headers={"X-Token": "DataDog"})
    assert response.status_code == 200
    assert response.json() == {"userid": "testUserID", "name": "Test User"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /users/{userid:str}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/testUserID"
    assert request_span.get_tag("http.status_code") == "200"


def test_mid_path_param_aggregate(client, tracer, test_spans):
    r = client.get("/users/testUserID/info", headers={"X-Token": "DataDog"})

    assert r.status_code == 200
    assert r.json() == {"User Info": "Here"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /users/{userid:str}/info"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/testUserID/info"
    assert request_span.get_tag("http.status_code") == "200"


def test_multi_path_param_aggregate(client, tracer, test_spans):
    response = client.get("/users/testUserID/name", headers={"X-Token": "DataDog"})

    assert response.status_code == 200
    assert response.json() == {"User Attribute": "Test User"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /users/{userid:str}/{attribute:str}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/testUserID/name"
    assert request_span.get_tag("http.status_code") == "200"


def test_distributed_tracing(client, tracer, test_spans):
    headers = [
        (http_propagation.HTTP_HEADER_PARENT_ID, "5555"),
        (http_propagation.HTTP_HEADER_TRACE_ID, "9999"),
        ("sleep", "False"),
    ]
    response = client.get("http://testserver/", headers=dict(headers))

    assert response.status_code == 200
    assert response.json() == {"Homepage Read": "Success"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.parent_id == 5555
    assert request_span.trace_id == 9999


@pytest.mark.asyncio
async def test_multiple_requests(application, tracer, test_spans):
    with override_http_config("fastapi", dict(trace_query_string=True)):
        async with httpx.AsyncClient(app=application) as client:
            responses = await asyncio.gather(
                client.get("http://testserver/", headers={"sleep": "True"}),
                client.get("http://testserver/", headers={"sleep": "False"}),
            )

    assert len(responses) == 2
    assert [r.status_code for r in responses] == [200] * 2
    assert [r.json() for r in responses] == [{"Homepage Read": "Sleep"}, {"Homepage Read": "Success"}]

    spans = test_spans.pop_traces()
    assert len(spans) == 2
    assert len(spans[0]) == 1
    assert len(spans[1]) == 1

    r1_span = spans[0][0]
    assert r1_span.service == "fastapi"
    assert r1_span.name == "fastapi.request"
    assert r1_span.resource == "GET /"
    assert r1_span.get_tag("http.method") == "GET"
    assert r1_span.get_tag("http.url") == "http://testserver/"

    r2_span = spans[1][0]
    assert r2_span.service == "fastapi"
    assert r2_span.name == "fastapi.request"
    assert r2_span.resource == "GET /"
    assert r2_span.get_tag("http.method") == "GET"
    assert r2_span.get_tag("http.url") == "http://testserver/"
    assert r1_span.trace_id != r2_span.trace_id


def test_service_can_be_overridden(client, tracer, test_spans):
    with override_config("fastapi", dict(service_name="test-override-service")):
        response = client.get("/", headers={"sleep": "False"})
        assert response.status_code == 200

    spans = test_spans.pop_traces()
    assert len(spans) > 0

    span = spans[0][0]
    assert span.service == "test-override-service"


@snapshot()
def test_table_query_snapshot(snapshot_client):
    r_post = snapshot_client.post(
        "/items/",
        headers={"X-Token": "DataDog"},
        json={"id": "test_id", "name": "Test Name", "description": "This request adds a new entry to the test db"},
    )
    assert r_post.status_code == 200
    assert r_post.json() == {
        "id": "test_id",
        "name": "Test Name",
        "description": "This request adds a new entry to the test db",
    }

    r_get = snapshot_client.get("/items/test_id", headers={"X-Token": "DataDog"})
    assert r_get.status_code == 200
    assert r_get.json() == {
        "id": "test_id",
        "name": "Test Name",
        "description": "This request adds a new entry to the test db",
    }
