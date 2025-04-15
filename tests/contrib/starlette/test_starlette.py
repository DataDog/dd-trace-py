import asyncio
import os

import httpx
import pytest
import sqlalchemy
import starlette
from starlette.testclient import TestClient

import ddtrace
from ddtrace.constants import ERROR_MSG
from ddtrace.contrib.internal.sqlalchemy.patch import patch as sql_patch
from ddtrace.contrib.internal.sqlalchemy.patch import unpatch as sql_unpatch
from ddtrace.contrib.internal.starlette.patch import patch as starlette_patch
from ddtrace.contrib.internal.starlette.patch import unpatch as starlette_unpatch
from ddtrace.internal import core
from ddtrace.propagation import http as http_propagation
from ddtrace.trace import Pin
from tests.contrib.starlette.app import get_app
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import override_global_config
from tests.utils import override_http_config
from tests.utils import snapshot


starlette_version_str = getattr(starlette, "__version__", "0.0.0")
starlette_version = tuple([int(i) for i in starlette_version_str.split(".")[:3]])


@pytest.fixture
def engine():
    sql_patch()
    engine = sqlalchemy.create_engine("sqlite:///test.db")
    yield engine
    sql_unpatch()


@pytest.fixture
def tracer(engine):
    original_tracer = ddtrace.tracer
    tracer = DummyTracer()
    Pin._override(engine, tracer=tracer)
    ddtrace.tracer = tracer
    core.tracer = tracer
    starlette_patch()
    yield tracer
    ddtrace.tracer = original_tracer
    core.tracer = original_tracer
    starlette_unpatch()


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


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
    starlette_patch()
    app = get_app(engine)
    yield app
    starlette_unpatch()


@pytest.fixture
def snapshot_client(snapshot_app):
    with TestClient(snapshot_app) as test_client:
        yield test_client


@pytest.fixture
def snapshot_app_with_tracer(tracer, engine):
    starlette_patch()
    app = get_app(engine)
    yield app
    starlette_unpatch()


@pytest.fixture
def snapshot_client_with_tracer(snapshot_app_with_tracer):
    with TestClient(snapshot_app_with_tracer) as test_client:
        yield test_client


def test_200(client, tracer, test_spans):
    r = client.get("/200")

    assert r.status_code == 200
    assert r.text == "Success"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /200"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/200"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_200_query_string(client, tracer, test_spans):
    with override_http_config("starlette", dict(trace_query_string=True)):
        r = client.get("?foo=bar")

    assert r.status_code == 200
    assert r.text == "Success"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/?foo=bar"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "foo=bar"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_200_multi_query_string(client, tracer, test_spans):
    with override_http_config("starlette", dict(trace_query_string=True)):
        r = client.get("?foo=bar&x=y")

    assert r.status_code == 200
    assert r.text == "Success"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/?foo=bar&x=y"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("http.query.string") == "foo=bar&x=y"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_201(client, tracer, test_spans):
    r = client.post("/201")

    assert r.status_code == 201
    assert r.text == "Created"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "POST /201"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "POST"
    assert request_span.get_tag("http.url") == "http://testserver/201"
    assert request_span.get_tag("http.status_code") == "201"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_404(client, tracer, test_spans):
    r = client.get("/404")

    assert r.status_code == 404
    assert r.text == "Not Found"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /404"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/404"
    assert request_span.get_tag("http.status_code") == "404"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_500error(client, tracer, test_spans):
    with pytest.raises(RuntimeError):
        client.get("/500")

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /500"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/500"
    assert request_span.get_tag("http.status_code") == "500"
    assert request_span.get_tag(ERROR_MSG) == "Server error"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"
    assert 'raise RuntimeError("Server error")' in request_span.get_tag("error.stack")


def test_distributed_tracing(client, tracer, test_spans):
    headers = [
        (http_propagation.HTTP_HEADER_PARENT_ID, "1234"),
        (http_propagation.HTTP_HEADER_TRACE_ID, "5678"),
    ]
    r = client.get("http://testserver/", headers=dict(headers))

    assert r.status_code == 200
    assert r.text == "Success"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.parent_id == 1234
    assert request_span.trace_id == 5678
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


@pytest.mark.asyncio
async def test_multiple_requests(app, tracer, test_spans):
    with override_http_config("starlette", dict(trace_query_string=True)):
        async with httpx.AsyncClient(app=app) as client:
            responses = await asyncio.gather(
                client.get("http://testserver/", params={"sleep": True}),
                client.get("http://testserver/", params={"sleep": True}),
            )

    assert len(responses) == 2
    assert [r.status_code for r in responses] == [200] * 2
    assert [r.text for r in responses] == ["Success"] * 2

    r1_span, r2_span = list(test_spans.filter_spans(name="starlette.request"))
    assert r1_span.service == "starlette"
    assert r1_span.name == "starlette.request"
    assert r1_span.resource == "GET /"
    assert r1_span.get_tag("http.method") == "GET"
    assert r1_span.get_tag("http.url") == "http://testserver/?sleep=true"
    assert r1_span.get_tag("http.query.string") == "sleep=true"

    assert r2_span.service == "starlette"
    assert r2_span.name == "starlette.request"
    assert r2_span.resource == "GET /"
    assert r2_span.get_tag("http.method") == "GET"
    assert r2_span.get_tag("http.url") == "http://testserver/?sleep=true"
    assert r2_span.get_tag("http.query.string") == "sleep=true"


def test_streaming_response(client, tracer, test_spans):
    r = client.get("/stream")

    assert r.status_code == 200

    assert r.text.endswith("streaming")

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /stream"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/stream"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_file_response(client, tracer, test_spans):
    r = client.get("/file")

    assert r.status_code == 200
    assert r.text == "Datadog says hello!"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /file"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/file"
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_invalid_path_param(client, tracer, test_spans):
    r = client.get("/users/test")

    assert r.status_code == 404
    assert r.text == "Not Found"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/test"
    assert request_span.get_tag("http.route") is None
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/test"
    assert request_span.get_tag("http.status_code") == "404"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_path_param_aggregate(client, tracer, test_spans):
    r = client.get("/users/1")

    assert r.status_code == 200
    assert r.text == "Success"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/{userid:int}"
    assert request_span.get_tag("http.route") == "/users/{userid:int}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/1"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_mid_path_param_aggregate(client, tracer, test_spans):
    r = client.get("/users/1/info")

    assert r.status_code == 200
    assert r.text == "Success"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/{userid:int}/info"
    assert request_span.get_tag("http.route") == "/users/{userid:int}/info"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/1/info"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_multi_path_param_aggregate(client, tracer, test_spans):
    r = client.get("/users/1/name")

    assert r.status_code == 200
    assert r.text == "Success"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.service == "starlette"
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /users/{userid:int}/{attribute:str}"
    assert request_span.get_tag("http.route") == "/users/{userid:int}/{attribute:str}"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("http.url") == "http://testserver/users/1/name"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "starlette"
    assert request_span.get_tag("span.kind") == "server"


def test_table_query(client, tracer, test_spans):
    r = client.post("/notes", json={"id": 1, "text": "test", "completed": 1})
    assert r.status_code == 200
    assert r.text == "Success"

    starlette_span = next(test_spans.filter_spans(name="starlette.request"))
    assert starlette_span.service == "starlette"
    assert starlette_span.name == "starlette.request"
    assert starlette_span.resource == "POST /notes"
    assert starlette_span.error == 0
    assert starlette_span.get_tag("http.method") == "POST"
    assert starlette_span.get_tag("http.url") == "http://testserver/notes"
    assert starlette_span.get_tag("http.status_code") == "200"
    assert starlette_span.get_tag("component") == "starlette"
    assert starlette_span.get_tag("span.kind") == "server"

    sql_span = next(test_spans.filter_spans(name="sqlite.query", trace_id=starlette_span.trace_id))
    assert sql_span.service == "sqlite"
    assert sql_span.name == "sqlite.query"
    assert sql_span.resource == "INSERT INTO notes (id, text, completed) VALUES (?, ?, ?)"
    assert sql_span.error == 0
    assert sql_span.get_tag("sql.db") == "test.db"
    assert sql_span.get_tag("component") == "sqlalchemy"
    assert sql_span.get_tag("span.kind") == "client"

    test_spans.reset()

    r = client.get("/notes")

    assert r.status_code == 200
    assert r.text == "[{'id': 1, 'text': 'test', 'completed': 1}]"

    starlette_span = next(test_spans.filter_spans(name="starlette.request"))
    assert starlette_span.service == "starlette"
    assert starlette_span.name == "starlette.request"
    assert starlette_span.resource == "GET /notes"
    assert starlette_span.error == 0
    assert starlette_span.get_tag("http.method") == "GET"
    assert starlette_span.get_tag("http.url") == "http://testserver/notes"
    assert starlette_span.get_tag("http.status_code") == "200"
    assert starlette_span.get_tag("component") == "starlette"
    assert starlette_span.get_tag("span.kind") == "server"

    sql_span = next(test_spans.filter_spans(name="sqlite.query", trace_id=starlette_span.trace_id))
    assert sql_span.service == "sqlite"
    assert sql_span.name == "sqlite.query"
    assert sql_span.resource == "SELECT * FROM NOTES"
    assert sql_span.error == 0
    assert sql_span.get_tag("sql.db") == "test.db"
    assert sql_span.get_tag("component") == "sqlalchemy"
    assert sql_span.get_tag("span.kind") == "client"


@pytest.mark.parametrize("host", ["hostserver", "hostserver:5454"])
def test_host_header(client, tracer, test_spans, host):
    r = client.get("/200", headers={"host": host})

    assert r.status_code == 200
    assert r.text == "Success"

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.get_tag("http.url") == "http://%s/200" % (host,)


@snapshot()
def test_subapp_snapshot(snapshot_client):
    response = snapshot_client.get("/sub-app/hello/name")
    assert response.status_code == 200
    assert response.text == "Success"


@snapshot()
def test_subapp_two_snapshot(snapshot_client):
    response = snapshot_client.get("/sub-app-two/hello/name")
    assert response.status_code == 200
    assert response.text == "Success"


@snapshot()
def test_subapp_nested_snapshot(snapshot_client):
    response = snapshot_client.get("/sub-app-nested/nested-app/hello/name")
    assert response.status_code == 200
    assert response.text == "Success"


@snapshot()
def test_subapp_nested_call_snapshot(snapshot_client):
    response = snapshot_client.get("/sub-app-nested/hello")
    assert response.status_code == 200
    assert response.text == "Success"


@snapshot()
def test_table_query_snapshot(snapshot_client):
    r_post = snapshot_client.post("/notes", json={"id": 1, "text": "test", "completed": 1})

    assert r_post.status_code == 200
    assert r_post.text == "Success"

    r_get = snapshot_client.get("/notes")
    assert r_get.status_code == 200
    assert r_get.text == "[{'id': 1, 'text': 'test', 'completed': 1}]"


@pytest.mark.skipif(
    starlette_version >= (0, 21, 0), reason="Starlette>=0.21.0 replaced requests with httpx in TestClient"
)
@snapshot()
def test_incorrect_patching(run_python_code_in_subprocess):
    """
    When Starlette is patched after the app is created
        We create no traces
        We do not crash the application
        We log a warning
    """
    code = """
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient
import sqlalchemy

from ddtrace._monkey import _patch_all


from tests.contrib.starlette.app import get_app

engine = sqlalchemy.create_engine("sqlite:///test.db")
app = get_app(engine)

# Calling _patch_all late
# DEV: The test client uses `requests` so we want to ignore them for this scenario
_patch_all(requests=False, http=False)
with TestClient(app) as test_client:
    r = test_client.get("/200")

    assert r.status_code == 200
    assert r.text == "Success"
    """

    out, err, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, err
    assert out == b"", err
    assert err == b"datadog context not present in ASGI request scope, trace middleware may be missing\n"


# Ignoring span link attributes until values are
# normalized: https://github.com/DataDog/dd-apm-test-agent/issues/154
@snapshot(ignores=["meta._dd.span_links"])
def test_background_task(snapshot_client_with_tracer, tracer, test_spans):
    """Tests if background tasks have been excluded from span duration"""
    r = snapshot_client_with_tracer.get("/backgroundtask")
    assert r.status_code == 200
    assert r.text == '{"result":"Background task added"}'

    request_span = next(test_spans.filter_spans(name="starlette.request"))
    assert request_span.name == "starlette.request"
    assert request_span.resource == "GET /backgroundtask"
    # typical duration without background task should be in less than 10ms
    # duration with background task will take approximately 1.1s
    assert request_span.duration < 1

    # Background task shound not be a child of the request span
    background_span = next(test_spans.filter_spans(name="starlette.background_task"))
    # background task should link to the request span
    assert background_span
    assert background_span.parent_id is None
    [link, *others] = [link for link in background_span._links if link.span_id == request_span.span_id]
    assert not others
    assert link.trace_id == request_span.trace_id
    assert link.span_id == request_span.span_id
    assert background_span.name == "starlette.background_task"
    assert background_span.resource == "custom_task"


@pytest.mark.parametrize(
    "service_schema",
    [
        (None, None),
        (None, "v0"),
        (None, "v1"),
        ("mysvc", None),
        ("mysvc", "v0"),
        ("mysvc", "v1"),
    ],
)
def test_schematization(ddtrace_run_python_code_in_subprocess, service_schema):
    service, schema = service_schema
    code = """
import pytest
import sys
from ddtrace import config
import sqlalchemy

from tests.contrib.starlette.test_starlette import snapshot_app
from tests.contrib.starlette.test_starlette import snapshot_client

@pytest.fixture
def engine():
    engine = sqlalchemy.create_engine("sqlite:///test.db")
    yield engine

def test(snapshot_client):
    response = snapshot_client.get("/sub-app/hello/name")

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """
    env = os.environ.copy()
    if service:
        env["DD_SERVICE"] = service
    if schema:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema
    # We only care about the starlette traces
    env["DD_TRACE_SQLALCHEMY_ENABLED"] = "false"
    env["DD_TRACE_SQLITE3_ENABLED"] = "false"
    env["DD_TRACE_HTTPX_ENABLED"] = "false"
    env["DD_TRACE_REQUESTS_ENABLED"] = "false"
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
    assert err == b"", err.decode()


def test_inferred_spans_api_gateway(client, test_spans):
    with override_global_config(dict(_inferred_proxy_services_enabled=True)):
        headers = {
            "x-dd-proxy": "aws-apigateway",
            "x-dd-proxy-request-time-ms": "1736973768000",
            "x-dd-proxy-path": "/",
            "x-dd-proxy-httpmethod": "GET",
            "x-dd-proxy-domain-name": "local",
            "x-dd-proxy-stage": "stage",
        }

        client.get("/200", headers=headers)
        web_span = test_spans.find_span(name="starlette.request")
        aws_gateway_span = test_spans.find_span(name="aws.apigateway")

        assert_web_and_inferred_aws_api_gateway_span_data(
            aws_gateway_span,
            web_span,
            web_span_name="starlette.request",
            web_span_component="starlette",
            web_span_service_name="starlette",
            web_span_resource="GET /200",
            api_gateway_service_name="local",
            api_gateway_resource="GET /",
            method="GET",
            status_code="200",
            url="local/",
            start=1736973768,
        )
