from fastapi import Request
from fastapi.responses import PlainTextResponse
import pytest

from ddtrace.appsec._handlers import _on_asgi_request_parse_body
from ddtrace.internal import core
import tests.appsec.rules as rules
from tests.utils import override_global_config


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
    @fastapi_application.post("/index.html")
    async def test_route(request: Request):
        body = await request._receive()
        return PlainTextResponse(body["body"])

    payload, content_type = '{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}', "application/json"

    with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
        # disable callback
        # test if asgi middleware is ok without any callback registered
        core.reset_listeners(event_id="asgi.request.parse.body")
        resp = client.post(
            "/index.html?args=test",
            data=payload,
            headers={"Content-Type": content_type},
        )
        assert resp.status_code == 200
        assert get_response_body(resp) == '{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}'
    with override_global_config(dict(_asm_enabled=True)):
        resp = client.post(
            "/index.html?args=test",
            data=payload,
            headers={"Content-Type": content_type},
        )
        assert resp.status_code == 200
        assert get_response_body(resp) == '{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}'
