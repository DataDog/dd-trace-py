import asyncio
import os
import time

import fastapi
from fastapi.testclient import TestClient
import httpx
import pytest

from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.starlette.patch import patch as patch_starlette
from ddtrace.contrib.internal.starlette.patch import unpatch as unpatch_starlette
from ddtrace.internal.utils.version import parse_version
from ddtrace.propagation import http as http_propagation
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import override_config
from tests.utils import override_global_config
from tests.utils import override_http_config
from tests.utils import snapshot


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
async def test_multiple_requests(fastapi_application, tracer, test_spans):
    with override_http_config("fastapi", dict(trace_query_string=True)):
        async with httpx.AsyncClient(app=fastapi_application) as client:
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


def _run_websocket_test():
    import fastapi  # noqa: F401
    from fastapi.testclient import TestClient

    from ddtrace.contrib.internal.fastapi.patch import patch as fastapi_patch
    from ddtrace.contrib.internal.fastapi.patch import unpatch as fastapi_unpatch
    from tests.contrib.fastapi import app

    fastapi_patch()
    try:
        application = app.get_app()
        with TestClient(application) as client:
            with client.websocket_connect("/ws") as websocket:
                websocket.send_text("message")
                websocket.receive_text()
                websocket.send_text("close")
    finally:
        fastapi_unpatch()


@pytest.mark.subprocess(env=dict(DD_TRACE_WEBSOCKET_MESSAGES_ENABLED="true"))
@snapshot(ignores=["meta._dd.span_links", "metrics.websocket.message.length"])
def test_traced_websocket(test_spans, snapshot_app):
    from tests.contrib.fastapi.test_fastapi import _run_websocket_test

    _run_websocket_test()


@pytest.mark.subprocess(
    env=dict(
        DD_TRACE_WEBSOCKET_MESSAGES_ENABLED="true",
        DD_TRACE_WEBSOCKET_MESSAGES_INHERIT_SAMPLING="false",
    )
)
@snapshot(ignores=["meta._dd.span_links"])
def test_websocket_tracing_sampling_not_inherited(test_spans, snapshot_app):
    from tests.contrib.fastapi.test_fastapi import _run_websocket_test

    _run_websocket_test()


@pytest.mark.subprocess(
    env=dict(
        DD_TRACE_WEBSOCKET_MESSAGES_ENABLED="true",
        DD_TRACE_WEBSOCKET_MESSAGES_SEPARATE_TRACES="false",
    )
)
@snapshot(ignores=["meta._dd.span_links", "metrics.websocket.message.length"])
def test_websocket_tracing_not_separate_traces(test_spans, snapshot_app):
    from tests.contrib.fastapi.test_fastapi import _run_websocket_test

    _run_websocket_test()


@pytest.mark.snapshot(ignores=["meta._dd.span_links", "metrics.websocket.message.length"])
def test_long_running_websocket_session(test_spans, snapshot_app):
    client = TestClient(snapshot_app)

    with override_config("fastapi", dict(_trace_asgi_websocket_messages=True)):
        with client.websocket_connect("/ws") as websocket:
            data = websocket.receive_json()
            assert data == {"test": "Hello WebSocket"}

            # simulate a long-running session with multiple messages
            for i in range(5):
                websocket.send_text(f"ping {i}")
                response = websocket.receive_text()
                assert f"pong {i}" in response
                time.sleep(0.1)

            websocket.send_text("goodbye")
            farewell = websocket.receive_text()
            assert "bye" in farewell


@pytest.mark.snapshot(ignores=["meta._dd.span_links", "metrics.websocket.message.length"])
def test_websocket_sampling_inherited(test_spans, snapshot_app):
    client = TestClient(snapshot_app)

    with override_config("fastapi", dict(_trace_asgi_websocket_messages=True)):
        with client.websocket_connect("/ws") as websocket:
            websocket.send_text("message")
            websocket.receive_text()
            websocket.send_text("close")

    traces = test_spans.pop_traces()
    spans = [span for trace in traces for span in trace]

    handshake_span = next((s for s in spans if s.name == "fastapi.request"), None)
    assert handshake_span, "fastapi.request (handshake) span not found"
    handshake_span._meta["_dd.p.dm"] = "-1"
    child_spans = [s for s in spans if s.trace_id == handshake_span.trace_id and s.span_id != handshake_span.span_id]

    for span in child_spans:
        assert "_dd.p.dm" not in span._meta, f"Child span {span.name} should not override _dd.p.dm"


def test_dont_trace_websocket_by_default(client, test_spans):
    initial_event_count = len(test_spans.pop_traces())
    with client.websocket_connect("/ws") as websocket:
        data = websocket.receive_json()
        assert data == {"test": "Hello WebSocket"}
        websocket.send_text("ping")
        spans = test_spans.pop_traces()
        assert len(spans) <= initial_event_count


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
    [link, *others] = [link for link in background_span._links if link.span_id == request_span.span_id]
    assert not others
    assert link.trace_id == request_span.trace_id
    assert link.span_id == request_span.span_id
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
        (None, "v1", DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME, "http.server.request"),
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

from tests.contrib.fastapi.conftest import snapshot_app
from tests.contrib.fastapi.conftest import snapshot_client

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


@pytest.mark.parametrize(
    "test",
    [
        {
            "endpoint": "/items/foo",
            "status_code": "200",
            "resource_suffix": "/items/{item_id}",
        },
        {"endpoint": "/500", "status_code": "500", "resource_suffix": "/500"},
    ],
)
@pytest.mark.parametrize(
    "test_headers",
    [
        {
            "type": "default",
            "headers": {
                "x-dd-proxy": "aws-apigateway",
                "x-dd-proxy-request-time-ms": "1736973768000",
                "x-dd-proxy-path": "/",
                "x-dd-proxy-httpmethod": "GET",
                "x-dd-proxy-domain-name": "local",
                "x-dd-proxy-stage": "stage",
                "X-Token": "DataDog",
            },
        },
        {
            "type": "distributed",
            "headers": {
                "x-dd-proxy": "aws-apigateway",
                "x-dd-proxy-request-time-ms": "1736973768000",
                "x-dd-proxy-path": "/",
                "x-dd-proxy-httpmethod": "GET",
                "x-dd-proxy-domain-name": "local",
                "x-dd-proxy-stage": "stage",
                "x-datadog-trace-id": "1",
                "x-datadog-parent-id": "2",
                "x-datadog-origin": "rum",
                "x-datadog-sampling-priority": "2",
                "X-Token": "DataDog",
            },
        },
    ],
)
@pytest.mark.parametrize("inferred_proxy_enabled", [True])
def test_inferred_spans_api_gateway(client, tracer, test_spans, test, inferred_proxy_enabled, test_headers):
    # When the inferred proxy feature is enabled, there should be an inferred span
    with override_global_config(dict(_inferred_proxy_services_enabled=inferred_proxy_enabled)):
        if test["status_code"] == "500":
            with pytest.raises(RuntimeError):
                client.get(test["endpoint"], headers=test_headers["headers"])
        else:
            client.get(test["endpoint"], headers=test_headers["headers"])

        if inferred_proxy_enabled:
            web_span = test_spans.find_span(name="fastapi.request")
            aws_gateway_span = test_spans.find_span(name="aws.apigateway")

            assert_web_and_inferred_aws_api_gateway_span_data(
                aws_gateway_span,
                web_span,
                web_span_name="fastapi.request",
                web_span_component="fastapi",
                web_span_service_name="fastapi",
                web_span_resource="GET " + test["resource_suffix"],
                api_gateway_service_name="local",
                api_gateway_resource="GET /",
                method="GET",
                status_code=test["status_code"],
                url="local/",
                start=1736973768,
                is_distributed=test_headers["type"] == "distributed",
                distributed_trace_id=1,
                distributed_parent_id=2,
                distributed_sampling_priority=USER_KEEP,
            )

        else:
            web_span = test_spans.find_span(name="fastapi.request")
            assert web_span._parent is None

            if test_headers["type"] == "distributed":
                assert web_span.trace_id == 1


def test_baggage_span_tagging_default(client, tracer, test_spans):
    response = client.get("/", headers={"baggage": "user.id=123,account.id=456,region=us-west"})

    assert response.status_code == 200

    spans = test_spans.pop_traces()
    # Assume the request span is the first span in the first trace.
    request_span = spans[0][0]

    assert request_span.get_tag("baggage.user.id") == "123"
    assert request_span.get_tag("baggage.account.id") == "456"
    # Since "region" is not in the default list, its baggage tag should not be present.
    assert request_span.get_tag("baggage.region") is None


def test_baggage_span_tagging_no_headers(client, tracer, test_spans):
    response = client.get("/", headers={})
    assert response.status_code == 200

    spans = test_spans.pop_traces()
    request_span = spans[0][0]

    # None of the baggage tags should be present.
    assert request_span.get_tag("baggage.user.id") is None
    assert request_span.get_tag("baggage.account.id") is None
    assert request_span.get_tag("baggage.session.id") is None


def test_baggage_span_tagging_empty_baggage(client, tracer, test_spans):
    response = client.get("/", headers={"baggage": ""})
    assert response.status_code == 200

    spans = test_spans.pop_traces()
    request_span = spans[0][0]

    # None of the baggage tags should be present.
    assert request_span.get_tag("baggage.user.id") is None
    assert request_span.get_tag("baggage.account.id") is None
    assert request_span.get_tag("baggage.session.id") is None


def test_baggage_span_tagging_baggage_api(client, tracer, test_spans):
    response = client.get("/", headers={"baggage": ""})
    assert response.status_code == 200

    spans = test_spans.pop_traces()
    request_span = spans[0][0]
    request_span.context.set_baggage_item("user.id", "123")
    # None of the baggage tags should be present since we only tag baggage during extraction from headers
    assert request_span.get_tag("baggage.account.id") is None
    assert request_span.get_tag("baggage.user.id") is None
    assert request_span.get_tag("baggage.session.id") is None
