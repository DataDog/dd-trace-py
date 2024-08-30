import json
import sys
import typing

from fastapi import Cookie
from fastapi import Header
from fastapi import Request
from fastapi import __version__ as _fastapi_version
from fastapi.responses import JSONResponse
import pytest

from ddtrace.appsec._handlers import _on_asgi_request_parse_body
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._patch import _on_iast_fastapi_patch
from ddtrace.internal import core
from tests.utils import override_env
from tests.utils import override_global_config


IAST_ENV = {"DD_IAST_REQUEST_SAMPLING": "100"}

fastapi_version = tuple([int(v) for v in _fastapi_version.split(".")])


def _aux_appsec_prepare_tracer(tracer):
    _on_iast_fastapi_patch()
    oce.reconfigure()

    tracer._iast_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")


def get_response_body(response):
    return response.text


def get_root_span(spans):
    return spans.pop_traces()[0][0]


@pytest.fixture
def setup_core_ok_after_test():
    yield
    core.on("asgi.request.parse.body", _on_asgi_request_parse_body, "await_receive_and_body")


@pytest.mark.usefixtures("setup_core_ok_after_test")
def test_query_param_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
        from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
        from ddtrace.appsec._iast._taint_tracking import origin_to_str

        query_params = request.query_params.get("iast_queryparam")
        ranges_result = get_tainted_ranges(query_params)

        return JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    # test if asgi middleware is ok without any callback registered
    core.reset_listeners(event_id="asgi.request.parse.body")

    with override_global_config(dict(_iast_enabled=True)), override_env(IAST_ENV):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html?iast_queryparam=test1234",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "test1234"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.parameter"


@pytest.mark.usefixtures("setup_core_ok_after_test")
def test_header_value_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
        from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
        from ddtrace.appsec._iast._taint_tracking import origin_to_str

        query_params = request.headers.get("iast_header")
        ranges_result = get_tainted_ranges(query_params)

        return JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    # test if asgi middleware is ok without any callback registered
    core.reset_listeners(event_id="asgi.request.parse.body")

    with override_global_config(dict(_iast_enabled=True)), override_env(IAST_ENV):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html",
            headers={"iast_header": "test1234"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "test1234"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.header"


@pytest.mark.usefixtures("setup_core_ok_after_test")
@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated was introduced on 3.9")
@pytest.mark.skipif(fastapi_version < (0, 95, 0), reason="Header annotation doesn't work on fastapi 94 or lower")
def test_header_value_source_typing_param(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(iast_header: typing.Annotated[str, Header()] = None):
        from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
        from ddtrace.appsec._iast._taint_tracking import origin_to_str

        ranges_result = get_tainted_ranges(iast_header)

        return JSONResponse(
            {
                "result": iast_header,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    # test if asgi middleware is ok without any callback registered
    core.reset_listeners(event_id="asgi.request.parse.body")

    with override_global_config(dict(_iast_enabled=True)), override_env(IAST_ENV):
        _aux_appsec_prepare_tracer(tracer)

        resp = client.get(
            "/index.html",
            headers={"iast-header": "test1234"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "test1234"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.header"


@pytest.mark.usefixtures("setup_core_ok_after_test")
def test_cookies_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
        from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
        from ddtrace.appsec._iast._taint_tracking import origin_to_str

        query_params = request.cookies.get("iast_cookie")
        ranges_result = get_tainted_ranges(query_params)
        return JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    # test if asgi middleware is ok without any callback registered
    core.reset_listeners(event_id="asgi.request.parse.body")

    with override_global_config(dict(_iast_enabled=True)), override_env(IAST_ENV):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html",
            cookies={"iast_cookie": "test1234"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "test1234"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.cookie.value"


@pytest.mark.usefixtures("setup_core_ok_after_test")
@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated was introduced on 3.9")
@pytest.mark.skipif(fastapi_version < (0, 95, 0), reason="Cookie annotation doesn't work on fastapi 94 or lower")
def test_cookies_source_typing_param(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(iast_cookie: typing.Annotated[str, Cookie()] = "ddd"):
        from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
        from ddtrace.appsec._iast._taint_tracking import origin_to_str

        ranges_result = get_tainted_ranges(iast_cookie)

        return JSONResponse(
            {
                "result": iast_cookie,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    # test if asgi middleware is ok without any callback registered
    core.reset_listeners(event_id="asgi.request.parse.body")

    with override_global_config(dict(_iast_enabled=True)), override_env(IAST_ENV):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html",
            cookies={"iast_cookie": "test1234"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "test1234"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.cookie.value"


@pytest.mark.usefixtures("setup_core_ok_after_test")
def test_path_param_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html/{item_id}")
    async def test_route(item_id):
        from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
        from ddtrace.appsec._iast._taint_tracking import origin_to_str

        ranges_result = get_tainted_ranges(item_id)

        return JSONResponse(
            {
                "result": item_id,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    # test if asgi middleware is ok without any callback registered
    core.reset_listeners(event_id="asgi.request.parse.body")

    with override_global_config(dict(_iast_enabled=True)), override_env(IAST_ENV):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html/test1234/",
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "test1234"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.path.parameter"
