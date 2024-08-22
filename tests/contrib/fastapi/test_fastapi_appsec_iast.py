import json

from fastapi import Request
from fastapi.responses import JSONResponse
import pytest

from ddtrace.appsec._handlers import _on_asgi_request_parse_body
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._patch import _on_iast_fastapi_patch
from ddtrace.internal import core
from tests.utils import override_global_config


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
def test_core_callback_request_body(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
        from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges

        query_params = request.query_params.get("iast_queryparam")
        ranges_result = get_tainted_ranges(query_params)
        return JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
            }
        )

    # test if asgi middleware is ok without any callback registered
    core.reset_listeners(event_id="asgi.request.parse.body")

    with override_global_config(dict(_iast_enabled=True)):
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
