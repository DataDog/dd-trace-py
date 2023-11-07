import asyncio
import os
import random
import re

import pytest
from sanic import Sanic
from sanic import __version__ as sanic_version
from sanic.config import DEFAULT_CONFIG
from sanic.exceptions import InvalidUsage
from sanic.exceptions import ServerError
from sanic.response import json
from sanic.response import text
from sanic.server import HttpProtocol

from ddtrace import config
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.internal.schema.span_attribute_schema import _DEFAULT_SPAN_SERVICE_NAMES
from ddtrace.propagation import http as http_propagation
from tests.utils import override_config
from tests.utils import override_http_config


# Helpers for handling response objects across sanic versions

sanic_version = tuple(map(int, sanic_version.split(".")))


try:
    from sanic.response import ResponseStream

    def stream(*args, **kwargs):
        return ResponseStream(*args, **kwargs)

except ImportError:
    # stream was removed in sanic v22.6.0
    from sanic.response import stream


def _response_status(response):
    return getattr(response, "status_code", getattr(response, "status", None))


async def _response_json(response):
    resp_json = response.json
    if callable(resp_json):
        resp_json = response.json()
    if asyncio.iscoroutine(resp_json):
        resp_json = await resp_json
    return resp_json


async def _response_text(response):
    resp_text = response.text
    if callable(resp_text):
        resp_text = resp_text()
    if asyncio.iscoroutine(resp_text):
        resp_text = await resp_text
    return resp_text


@pytest.fixture
def app(tracer):
    # Sanic 20.12 and newer prevent loading multiple applications
    # with the same name if register is True.
    DEFAULT_CONFIG["REGISTER"] = False
    DEFAULT_CONFIG["RESPONSE_TIMEOUT"] = 1.0
    app = Sanic("sanic")

    @tracer.wrap()
    async def random_sleep():
        await asyncio.sleep(random.random() * 0.1)

    @app.route("/hello")
    async def hello(request):
        await random_sleep()
        return json({"hello": "world"})

    @app.route("/hello/<first_name>")
    async def hello_single_param(request, first_name):
        await random_sleep()
        return json({"hello": first_name})

    @app.route("/hello/<first_name>/<surname>")
    async def hello_multiple_params(request, first_name, surname):
        await random_sleep()
        return json({"hello": f"{first_name} {surname}"})

    @app.route("/stream_response")
    async def stream_response(request):
        async def sample_streaming_fn(response):
            await response.write("foo,")
            await response.write("bar")

        return stream(sample_streaming_fn, content_type="text/csv", headers={})

    @app.route("/error400")
    async def error_400(request):
        raise InvalidUsage("Something bad with the request")

    @app.route("/error")
    async def error(request):
        server_error = ServerError("Something bad happened")
        server_error.status_code = 500
        raise server_error

    @app.route("/invalid")
    async def invalid(request):
        return "This should fail"

    @app.route("/empty")
    async def empty(request):
        pass

    @app.route("/<n:int>/count", methods=["GET"])
    async def count(request, n):
        return json({"hello": n})

    @app.exception(ServerError)
    def handler_exception(request, exception):
        return text(exception.args[0], exception.status_code)

    yield app


# DEV: pytest-sanic is not compatible with sanic >= 21.9.0 so we instead
#     are using sanic_testing, but need to create a compatible fixture/API
if sanic_version >= (21, 9, 0):

    @pytest.fixture
    def client(app):
        from sanic_testing.testing import SanicASGITestClient

        # Create a test client compatible with pytest-sanic test client
        class TestClient(SanicASGITestClient):
            async def request(self, *args, **kwargs):
                request, response = await super(TestClient, self).request(*args, **kwargs)
                return response

        return TestClient(app)

else:

    @pytest.fixture
    @pytest.mark.asyncio
    async def client(sanic_client, app):
        return await sanic_client(app, protocol=HttpProtocol)


@pytest.fixture(
    params=[
        dict(),
        dict(service="mysanicsvc"),
        dict(analytics_enabled=False),
        dict(analytics_enabled=True),
        dict(analytics_enabled=True, analytics_sample_rate=0.5),
        dict(analytics_enabled=False, analytics_sample_rate=0.5),
        dict(distributed_tracing=False),
        dict(http_tag_query_string=True),
        dict(http_tag_query_string=False),
    ],
    ids=[
        "default",
        "service_override",
        "disable_analytics",
        "enable_analytics_default_sample_rate",
        "enable_analytics_custom_sample_rate",
        "disable_analytics_custom_sample_rate",
        "disable_distributed_tracing",
        "http_tag_query_string_enabled",
        "http_tag_query_string_disabled",
    ],
)
def integration_config(request):
    return request.param


@pytest.fixture(
    params=[
        dict(),
        dict(trace_query_string=False),
        dict(trace_query_string=True),
    ],
    ids=[
        "default",
        "disable trace query string",
        "enable trace query string",
    ],
)
def integration_http_config(request):
    return request.param


@pytest.mark.asyncio
async def test_basic_app(tracer, client, integration_config, integration_http_config, test_spans):
    """Test Sanic Patching"""
    with override_http_config("sanic", integration_http_config):
        with override_config("sanic", integration_config):
            headers = [
                (http_propagation.HTTP_HEADER_PARENT_ID, "1234"),
                (http_propagation.HTTP_HEADER_TRACE_ID, "5678"),
            ]
            response = await client.get("/hello", params=[("foo", "bar")], headers=headers)
            assert _response_status(response) == 200
            assert await _response_json(response) == {"hello": "world"}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("component") == "sanic"
    assert request_span.get_tag("span.kind") == "server"
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.resource == "GET /hello"

    sleep_span = spans[0][1]
    assert sleep_span.name == "tests.contrib.sanic.test_sanic.random_sleep"
    assert sleep_span.parent_id == request_span.span_id

    if integration_config.get("service"):
        assert request_span.service == integration_config["service"]
    else:
        assert request_span.service == "sanic"

    if integration_http_config.get("trace_query_string"):
        assert request_span.get_tag("http.query.string") == "foo=bar"
    else:
        assert request_span.get_tag("http.query.string") is None

    if integration_config.get("http_tag_query_string_enabled"):
        assert re.search(r"/hello\?foo\=bar$", request_span.get_tag("http.url"))

    if integration_config.get("http_tag_query_string_disabled"):
        assert re.search(r"/hello$", request_span.get_tag("http.url"))

    if integration_config.get("analytics_enabled"):
        analytics_sample_rate = integration_config.get("analytics_sample_rate") or 1.0
        assert request_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) == analytics_sample_rate
    else:
        assert request_span.get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    if integration_config.get("distributed_tracing", True):
        assert request_span.parent_id == 1234
        assert request_span.trace_id == 5678
    else:
        assert request_span.parent_id is None
        assert request_span.trace_id is not None and request_span.trace_id != 5678


@pytest.mark.parametrize(
    "url, expected_json, expected_resource",
    [
        ("/hello/foo", {"hello": "foo"}, "GET /hello/<first_name>"),
        ("/hello/foo/bar", {"hello": "foo bar"}, "GET /hello/<first_name>/<surname>"),
    ],
)
@pytest.mark.asyncio
async def test_resource_name(tracer, client, url, expected_json, expected_resource, test_spans):
    response = await client.get(url)
    assert _response_status(response) == 200
    assert await _response_json(response) == expected_json

    spans = test_spans.pop_traces()
    request_span = spans[0][0]
    assert request_span.resource == expected_resource


@pytest.mark.asyncio
async def test_streaming_response(tracer, client, test_spans):
    response = await client.get("/stream_response")
    assert _response_status(response) == 200
    assert (await _response_text(response)).endswith("foo,bar")

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 0
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("component") == "sanic"
    assert request_span.get_tag("span.kind") == "server"
    assert re.search("/stream_response$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "200"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,url,content",
    [(400, "/error400", "Something bad with the request"), (404, "/nonexistent", "not found")],
)
async def test_error_app(tracer, client, test_spans, status_code, url, content):
    response = await client.get(url)
    assert _response_status(response) == status_code
    assert content in await _response_text(response)

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"

    # We do not attach exception info for 404s
    assert request_span.error == 0
    assert request_span.get_tag(ERROR_MSG) is None
    assert request_span.get_tag(ERROR_TYPE) is None
    assert request_span.get_tag(ERROR_STACK) is None
    assert request_span.get_tag("component") == "sanic"
    assert request_span.get_tag("span.kind") == "server"

    assert request_span.get_tag("http.method") == "GET"
    assert re.search(f"{url}$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == str(status_code)


@pytest.mark.asyncio
async def test_exception(tracer, client, test_spans):
    response = await client.get("/error")
    assert _response_status(response) == 500
    assert "Something bad happened" in await _response_text(response)

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("component") == "sanic"
    assert request_span.get_tag("span.kind") == "server"
    assert re.search("/error$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "500"


@pytest.mark.asyncio
async def test_multiple_requests(tracer, client, test_spans):
    responses = await asyncio.gather(
        client.get("/hello"),
        client.get("/hello"),
    )

    assert len(responses) == 2
    assert [_response_status(r) for r in responses] == [200] * 2
    assert [await _response_json(r) for r in responses] == [{"hello": "world"}] * 2

    spans = test_spans.pop_traces()
    assert len(spans) == 2
    assert len(spans[0]) == 2
    assert len(spans[1]) == 2

    assert spans[0][0].name == "sanic.request"
    assert spans[0][1].name == "tests.contrib.sanic.test_sanic.random_sleep"
    assert spans[0][0].parent_id is None
    assert spans[0][1].parent_id == spans[0][0].span_id
    assert spans[1][0].name == "sanic.request"
    assert spans[1][1].name == "tests.contrib.sanic.test_sanic.random_sleep"
    assert spans[1][0].parent_id is None
    assert spans[1][1].parent_id == spans[1][0].span_id


@pytest.mark.asyncio
async def test_invalid_response_type_str(tracer, client, test_spans):
    response = await client.get("/invalid")
    assert _response_status(response) == 500

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert request_span.get_tag("component") == "sanic"
    assert request_span.get_tag("span.kind") == "server"
    assert re.search("/invalid$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "500"


@pytest.mark.asyncio
async def test_invalid_response_type_empty(tracer, client, test_spans):
    response = await client.get("/empty")
    assert _response_status(response) == 500

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "sanic.request"
    assert request_span.service == "sanic"
    assert request_span.error == 1
    assert request_span.get_tag("http.method") == "GET"
    assert re.search("/empty$", request_span.get_tag("http.url"))
    assert request_span.get_tag("http.query.string") is None
    assert request_span.get_tag("http.status_code") == "500"
    assert request_span.get_tag("component") == "sanic"
    assert request_span.get_tag("span.kind") == "server"


@pytest.mark.asyncio
async def test_http_request_header_tracing(tracer, client, test_spans):
    config.sanic.http.trace_headers(["my-header"])

    response = await client.get(
        "/hello",
        headers={
            "my-header": "my_value",
        },
    )
    assert _response_status(response) == 200

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span = spans[0][0]
    assert request_span.get_tag("http.request.headers.my-header") == "my_value"


@pytest.mark.asyncio
async def test_endpoint_with_numeric_arg(tracer, client, test_spans):
    response = await client.get("/42/count")
    assert _response_status(response) == 200
    assert (await _response_text(response)) == '{"hello":42}'


@pytest.mark.parametrize("service_name", [None, "mysvc"])
@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_service_name_schematization(ddtrace_run_python_code_in_subprocess, schema_version, service_name):
    expected_service_name = {
        None: service_name or "sanic",
        "v0": service_name or "sanic",
        "v1": service_name or _DEFAULT_SPAN_SERVICE_NAMES["v1"],
    }[schema_version]
    code = """
import asyncio
import pytest
import sys

# prevent circular import issue
import sanic

# import fixtures
from tests.conftest import *
from tests.contrib.sanic.conftest import *
from tests.contrib.sanic.test_sanic import app
from tests.contrib.sanic.test_sanic import client
from tests.contrib.sanic.test_sanic import integration_config
from tests.contrib.sanic.test_sanic import integration_http_config
from tests.contrib.sanic.test_sanic import _response_status

from ddtrace.propagation import http as http_propagation

async def test(client, integration_config, integration_http_config, test_spans):
    response = await client.get("/hello", params=[("foo", "bar")])
    assert _response_status(response) == 200

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span = spans[0][0]
    assert request_span.service == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service_name
    )

    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    if service_name is not None:
        env["DD_SERVICE"] = service_name
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, out or err


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_operation_name_schematization(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_operation_name = {None: "sanic.request", "v0": "sanic.request", "v1": "http.server.request"}[
        schema_version
    ]
    code = """
import asyncio
import pytest
import sys

# prevent circular import issue
import sanic

# import fixtures
from tests.conftest import *
from tests.contrib.sanic.conftest import *
from tests.contrib.sanic.test_sanic import app
from tests.contrib.sanic.test_sanic import client
from tests.contrib.sanic.test_sanic import integration_config
from tests.contrib.sanic.test_sanic import integration_http_config
from tests.contrib.sanic.test_sanic import _response_status

from ddtrace.propagation import http as http_propagation

async def test(client, integration_config, integration_http_config, test_spans):
    response = await client.get("/hello", params=[("foo", "bar")])
    assert _response_status(response) == 200

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 2
    request_span = spans[0][0]
    assert request_span.name == "{}"

if __name__ == "__main__":
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_operation_name
    )

    env = os.environ.copy()
    if schema_version is not None:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(
        code,
        env=env,
    )
    assert status == 0, out or err
