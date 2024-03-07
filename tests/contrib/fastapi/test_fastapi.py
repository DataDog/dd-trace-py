import asyncio
import os

import fastapi
from fastapi.testclient import TestClient
import httpx
import pytest

import ddtrace
from ddtrace.contrib.fastapi import patch as fastapi_patch
from ddtrace.contrib.fastapi import unpatch as fastapi_unpatch
from ddtrace.contrib.starlette.patch import patch as patch_starlette
from ddtrace.contrib.starlette.patch import unpatch as unpatch_starlette
from ddtrace.internal.schema.span_attribute_schema import _DEFAULT_SPAN_SERVICE_NAMES
from ddtrace.internal.utils.version import parse_version
from ddtrace.propagation import http as http_propagation
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import flaky
from tests.utils import override_config
from tests.utils import override_http_config
from tests.utils import snapshot

from . import app


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = DummyTracer()

    ddtrace.tracer = tracer
    fastapi_patch()
    yield tracer
    ddtrace.tracer = original_tracer
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
def snapshot_app_with_middleware():
    fastapi_patch()

    application = app.get_app()

    @application.middleware("http")
    async def traced_middlware(request, call_next):
        with ddtrace.tracer.trace("traced_middlware"):
            response = await call_next(request)
            return response

    yield application

    fastapi_unpatch()


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


@pytest.fixture
def snapshot_app_with_tracer(tracer):
    fastapi_patch()
    application = app.get_app()
    yield application
    fastapi_unpatch()


@pytest.fixture
def snapshot_client_with_tracer(snapshot_app_with_tracer):
    with TestClient(snapshot_app_with_tracer) as test_client:
        yield test_client


def assert_serialize_span(serialize_span):
    assert serialize_span.service == "fastapi"
    assert serialize_span.name == "fastapi.serialize_response"
    assert serialize_span.error == 0


def test_read_homepage(client, tracer, test_spans):
    response = client.get("/", headers={"sleep": "False"})
    assert response.status_code == 200
    assert response.json() == {"Homepage Read": "Success"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert serialize_span.service == "fastapi"
    assert serialize_span.name == "fastapi.serialize_response"
    assert serialize_span.error == 0


def test_read_item_success(client, tracer, test_spans):
    response = client.get("/items/foo", headers={"X-Token": "DataDog"})
    assert response.status_code == 200
    assert response.json() == {"id": "foo", "name": "Foo", "description": "This item's description is foo."}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /items/{item_id}"
    assert request_span.get_tag("http.route") == "/items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/foo"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert_serialize_span(serialize_span)


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
    assert request_span.get_tag("http.route") == "/items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/bar"
    assert request_span.get_tag("http.status_code") == "401"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"


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
    assert request_span.get_tag("http.route") == "/items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/foobar"
    assert request_span.get_tag("http.status_code") == "404"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"


def test_read_item_query_string(client, tracer, test_spans):
    with override_http_config("fastapi", dict(trace_query_string=True)):
        response = client.get("/items/foo?q=query", headers={"X-Token": "DataDog"})

    assert response.status_code == 200
    assert response.json() == {"id": "foo", "name": "Foo", "description": "This item's description is foo."}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /items/{item_id}"
    assert request_span.get_tag("http.route") == "/items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/foo?q=query"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "q=query"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert_serialize_span(serialize_span)


def test_200_multi_query_string(client, tracer, test_spans):
    with override_http_config("fastapi", dict(trace_query_string=True)):
        r = client.get("/items/foo?name=Foo&q=query", headers={"X-Token": "DataDog"})

    assert r.status_code == 200
    assert r.json() == {"id": "foo", "name": "Foo", "description": "This item's description is foo."}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /items/{item_id}"
    assert request_span.get_tag("http.route") == "/items/{item_id}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/items/foo?name=Foo&q=query"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "name=Foo&q=query"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert_serialize_span(serialize_span)


def test_create_item_success(client, tracer, test_spans):
    _id = "aawlieufghai3w4uhfg"
    response = client.post(
        "/items/",
        headers={"X-Token": "DataDog"},
        json={"id": _id, "name": "Foo Bar", "description": "The Foo Bartenders"},
    )
    assert response.status_code == 200
    assert response.json() == {"id": _id, "name": "Foo Bar", "description": "The Foo Bartenders"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]

    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "POST /items/"
    assert request_span.get_tag("http.route") == "/items/"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "POST"
    assert request_span.get_tag("http.url") == "http://testserver/items/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert_serialize_span(serialize_span)


def test_create_item_bad_token(client, tracer, test_spans):
    _id = "iughq8374yfg"
    response = client.post(
        "/items/",
        headers={"X-Token": "DataDoged"},
        json={"id": _id, "name": "Foo Bar", "description": "The Foo Bartenders"},
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
    assert request_span.get_tag("http.route") == "/items/"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "POST"
    assert request_span.get_tag("http.url") == "http://testserver/items/"
    assert request_span.get_tag("http.status_code") == "401"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"


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
    assert request_span.get_tag("http.route") == "/items/"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "POST"
    assert request_span.get_tag("http.url") == "http://testserver/items/"
    assert request_span.get_tag("http.status_code") == "400"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"


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
    assert request_span.get_tag("http.route") is None
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/invalid_path"
    assert request_span.get_tag("http.status_code") == "404"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"


@snapshot(ignores=["meta.error.stack"])
def test_500_error_raised(snapshot_client):
    with pytest.raises(RuntimeError):
        snapshot_client.get("/500")


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
    assert request_span.get_tag("http.route") == "/stream"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/stream"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"


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
    assert request_span.get_tag("http.route") == "/file"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/file"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"


def test_path_param_aggregate(client, tracer, test_spans):
    response = client.get("/users/testUserID", headers={"X-Token": "DataDog"})
    assert response.status_code == 200
    assert response.json() == {"userid": "testUserID", "name": "Test User"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /users/{userid:str}"
    assert request_span.get_tag("http.route") == "/users/{userid:str}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/testUserID"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert_serialize_span(serialize_span)


def test_mid_path_param_aggregate(client, tracer, test_spans):
    r = client.get("/users/testUserID/info", headers={"X-Token": "DataDog"})

    assert r.status_code == 200
    assert r.json() == {"User Info": "Here"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /users/{userid:str}/info"
    assert request_span.get_tag("http.route") == "/users/{userid:str}/info"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/testUserID/info"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert_serialize_span(serialize_span)


def test_multi_path_param_aggregate(client, tracer, test_spans):
    response = client.get("/users/testUserID/name", headers={"X-Token": "DataDog"})

    assert response.status_code == 200
    assert response.json() == {"User Attribute": "Test User"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /users/{userid:str}/{attribute:str}"
    assert request_span.get_tag("http.route") == "/users/{userid:str}/{attribute:str}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/testUserID/name"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert_serialize_span(serialize_span)


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
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]
    assert request_span.service == "fastapi"
    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.parent_id == 5555
    assert request_span.trace_id == 9999
    assert request_span.get_tag("component") == "fastapi"
    assert request_span.get_tag("span.kind") == "server"

    assert_serialize_span(serialize_span)


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
    assert len(spans[0]) == 2
    assert len(spans[1]) == 2

    r1_span, s1_span = spans[0]
    assert r1_span.service == "fastapi"
    assert r1_span.name == "fastapi.request"
    assert r1_span.resource == "GET /"
    assert r1_span.get_tag("http.method") == "GET"
    assert r1_span.get_tag("http.url") == "http://testserver/"

    assert_serialize_span(s1_span)

    r2_span, s2_span = spans[1]
    assert r2_span.service == "fastapi"
    assert r2_span.name == "fastapi.request"
    assert r2_span.resource == "GET /"
    assert r2_span.get_tag("http.method") == "GET"
    assert r2_span.get_tag("http.url") == "http://testserver/"
    assert r1_span.trace_id != r2_span.trace_id

    assert_serialize_span(s2_span)


def test_service_can_be_overridden(client, tracer, test_spans):
    with override_config("fastapi", dict(service_name="test-override-service")):
        response = client.get("/", headers={"sleep": "False"})
        assert response.status_code == 200

    spans = test_spans.pop_traces()
    assert len(spans) > 0

    span = spans[0][0]
    assert span.service == "test-override-service"


def test_w_patch_starlette(client, tracer, test_spans):
    patch_starlette()
    try:
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
        assert request_span.get_tag("http.route") == "/file"
        assert request_span.error == 0
        assert request_span.get_tag("http.method") == "GET"
        assert request_span.get_tag("http.url") == "http://testserver/file"
        assert request_span.get_tag("http.query.string") is None
        assert request_span.get_tag("http.status_code") == "200"
        assert request_span.get_tag("component") == "fastapi"
        assert request_span.get_tag("span.kind") == "server"
    finally:
        unpatch_starlette()


@snapshot()
def test_subapp_snapshot(snapshot_client):
    response = snapshot_client.get("/sub-app/hello/foo")
    assert response.status_code == 200


@snapshot(token_override="tests.contrib.fastapi.test_fastapi.test_subapp_snapshot")
def test_subapp_w_starlette_patch_snapshot(snapshot_client):
    # Test that patching starlette doesn't affect the spans generated
    patch_starlette()
    try:
        response = snapshot_client.get("/sub-app/hello/foo")
        assert response.status_code == 200
    finally:
        unpatch_starlette()


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


@snapshot()
def test_traced_websocket(test_spans, snapshot_app):
    client = TestClient(snapshot_app)
    with override_config("fastapi", dict(_trace_asgi_websocket=True)):
        with client.websocket_connect("/ws") as websocket:
            data = websocket.receive_json()
            assert data == {"test": "Hello WebSocket"}


def test_dont_trace_websocket_by_default(client, test_spans):
    initial_event_count = len(test_spans.pop_traces())
    with client.websocket_connect("/ws") as websocket:
        data = websocket.receive_json()
        assert data == {"test": "Hello WebSocket"}
        spans = test_spans.pop_traces()
        assert len(spans) <= initial_event_count


@flaky(1735812000)
# Ignoring span link attributes until values are
# normalized: https://github.com/DataDog/dd-apm-test-agent/issues/154
@snapshot(ignores=["meta._dd.span_links"])
def test_background_task(snapshot_client_with_tracer, tracer, test_spans):
    """Tests if background tasks have been traced but excluded from span duration"""
    response = snapshot_client_with_tracer.get("/asynctask")
    assert response.status_code == 200
    assert response.json() == "task added"
    spans = test_spans.pop_traces()
    # Generates two traces
    assert len(spans) == 2
    assert len(spans[0]) == 2
    request_span, serialize_span = spans[0]

    assert request_span.name == "fastapi.request"
    assert request_span.resource == "GET /asynctask"
    # typical duration without background task should be in less than 10 ms
    # duration with background task will take approximately 1.1s
    assert request_span.duration < 1

    # Background task shound not be a child of the request span
    assert len(spans[1]) == 1
    background_span = spans[1][0]
    # background task should link to the request span
    assert background_span.parent_id is None
    assert background_span._links[request_span.span_id].trace_id == request_span.trace_id
    assert background_span._links[request_span.span_id].span_id == request_span.span_id
    assert background_span.name == "fastapi.background_task"
    assert background_span.resource == "custom_task"


@pytest.mark.parametrize("host", ["hostserver", "hostserver:5454"])
def test_host_header(client, tracer, test_spans, host):
    """Tests if background tasks have been excluded from span duration"""
    r = client.get("/asynctask", headers={"host": host})
    assert r.status_code == 200

    assert test_spans.spans
    request_span = test_spans.spans[0]
    assert request_span.get_tag("http.url") == "http://%s/asynctask" % (host,)


@snapshot()
def test_tracing_in_middleware(snapshot_app_with_middleware):
    """Test if fastapi middlewares are traced"""
    with TestClient(snapshot_app_with_middleware) as test_client:
        r = test_client.get("/", headers={"sleep": "False"})
        assert r.status_code == 200


@pytest.mark.parametrize(
    "schema_tuples",
    [
        (None, None, "fastapi", "fastapi.request"),
        (None, "v0", "fastapi", "fastapi.request"),
        (None, "v1", _DEFAULT_SPAN_SERVICE_NAMES["v1"], "http.server.request"),
        ("mysvc", None, "mysvc", "fastapi.request"),
        ("mysvc", "v0", "mysvc", "fastapi.request"),
        ("mysvc", "v1", "mysvc", "http.server.request"),
    ],
)
@pytest.mark.snapshot(
    variants={
        "6_9": (0, 60) <= parse_version(fastapi.__version__) < (0, 90),
        "9": parse_version(fastapi.__version__) >= (0, 90),  # 0.90+ has an extra serialize step
    }
)
def test_schematization(ddtrace_run_python_code_in_subprocess, schema_tuples):
    service_override, schema_version, expected_service, expected_operation = schema_tuples
    code = """
import pytest
import sys

from tests.contrib.fastapi.test_fastapi import snapshot_app
from tests.contrib.fastapi.test_fastapi import snapshot_client

def test_read_homepage(snapshot_client):
    snapshot_client.get("/sub-app/hello/name")

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    if service_override:
        env["DD_SERVICE"] = service_override
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    env["DD_TRACE_REQUESTS_ENABLED"] = "false"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, out.decode()
    assert err == b"", err.decode()
