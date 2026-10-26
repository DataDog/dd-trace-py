"""Bottle blocking exercises the real AppSec listener and WAF."""

import builtins
import sys

import bottle
import pytest
import webtest

from ddtrace.appsec._asm_request_context import get_active_asm_context
from ddtrace.appsec._asm_request_context import set_blocked
from ddtrace.appsec._contrib.web_framework import subscribers as web_framework_subscribers
from ddtrace.appsec._utils import Block_config
from ddtrace.appsec.trace_utils import block_request
from ddtrace.contrib._events.web_framework import WebFrameworkBlockResponseEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.contrib._events.web_framework import WebFrameworkRequestInspectEvent
from ddtrace.contrib._events.web_framework import WebFrameworkResponseInspectEvent
from ddtrace.contrib.internal.bottle.patch import TracePlugin
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.span_bus import span_from_context
from tests.appsec.rules import RULES_SRB
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_config


@pytest.fixture(params=[True, False], ids=["apm-enabled", "apm-disabled"])
def appsec(request, tracer, monkeypatch):
    if not asm_config._asm_libddwaf_available:
        pytest.skip("libddwaf is unavailable")
    with override_global_config(
        {
            "_asm_enabled": True,
            "_iast_enabled": False,
            "_asm_static_rule_file": RULES_SRB,
            "_api_security_enabled": False,
            "_waf_timeout": 50_000.0,
            "apm_tracing_enabled": request.param,
        }
    ):
        monkeypatch.setattr(tracer, "enabled", request.param)
        yield
    core.dispatch("asm.switch_state")


@pytest.fixture
def app(appsec):
    app = bottle.Bottle()
    app.install(TracePlugin())
    return app


@pytest.fixture
def request_spans():
    spans = []

    def capture(ctx):
        if ctx.event.component == "bottle":
            spans.append(span_from_context(ctx))

    core.on("context.started.bottle.request", capture, name="bottle.appsec.test.capture")
    try:
        yield spans
    finally:
        core.reset_listeners("context.started.bottle.request", capture)


@pytest.mark.parametrize(
    "path,headers",
    [
        ("/hello?value=xtrace", {}),
        ("/.git", {}),
        ("/hello", {"X-Test": "01972498723465"}),
        ("/AiKfOeRcvG45", {}),
        ("/hello", {"Cookie": "session=jdfoSDGFkivRG_234"}),
    ],
)
def test_waf_blocks_before_callback(app, request_spans, path, headers):
    calls = []

    @app.route("/<path:path>")
    def handler(path):
        calls.append(path)
        return "allowed"

    response = webtest.TestApp(app).get(path, headers=headers, status=403)
    assert response.content_type == "application/json"
    assert response.body
    assert calls == []
    (span,) = request_spans
    assert span.finished
    assert span.error == 0
    assert_span_http_status_code(span, 403)
    assert get_active_asm_context() is None


@pytest.mark.parametrize("accept,content_type", [("application/json", "application/json"), ("text/html", "text/html")])
def test_sdk_block_interrupts_callback_and_cleans_up(app, request_spans, accept, content_type):
    calls = []

    @app.route("/blocked")
    def blocked():
        bottle.response.set_header("X-Application", "must-not-leak")
        bottle.response.set_cookie("session", "must-not-leak")
        calls.append("before")
        block_request()
        calls.append("after")
        return "must-not-return"

    @app.route("/allowed")
    def allowed():
        return "allowed"

    client = webtest.TestApp(app)
    response = client.get("/blocked", headers={"Accept": accept}, status=403)
    assert calls == ["before"]
    assert response.content_type == content_type
    assert "X-Application" not in response.headers
    assert "Set-Cookie" not in response.headers
    assert int(response.headers["Content-Length"]) == len(response.body)
    assert get_active_asm_context() is None
    assert client.get("/allowed").text == "allowed"
    assert all(span.finished for span in request_spans)
    assert request_spans[0].error == 0
    assert_span_http_status_code(request_spans[0], 403)


def test_sdk_block_preserves_custom_redirect(app, request_spans):
    @app.route("/redirect")
    def redirect():
        set_blocked(Block_config(type="none", status_code=303, location="/denied"))
        block_request()
        return "must-not-return"

    response = webtest.TestApp(app).get("/redirect", status=303)
    assert response.location == "/denied"
    assert response.body == b""
    assert_span_http_status_code(request_spans[0], 303)


@pytest.mark.parametrize("outcome", ["return", "raise"])
def test_caught_block_still_replaces_callback_outcome(app, request_spans, outcome):
    @app.route("/caught")
    def caught():
        try:
            block_request()
        except BlockingException:
            pass
        if outcome == "raise":
            raise bottle.HTTPError(500, "must-not-return")
        return "must-not-return"

    response = webtest.TestApp(app).get("/caught", status=403)
    assert b"must-not-return" not in response.body
    assert_span_http_status_code(request_spans[0], 403)
    assert request_spans[0].error == 0


@pytest.mark.parametrize("outcome", ["return", "raise", "local"])
def test_response_headers_can_trigger_blocking(app, request_spans, outcome):
    @app.route("/response")
    def handler():
        headers = {"X-Test": "MagicKey_Al4h7iCFep9s1"}
        if outcome == "local":
            bottle.response.set_header("X-Test", headers["X-Test"])
            return "must-not-return"
        result = bottle.HTTPResponse("must-not-return", status=202, headers=headers)
        if outcome == "raise":
            raise result
        return result

    response = webtest.TestApp(app).get("/response", status=403)
    assert "X-Test" not in response.headers
    assert b"must-not-return" not in response.body
    assert_span_http_status_code(request_spans[0], 403)


@pytest.mark.parametrize("close_fails", [False, True])
@pytest.mark.parametrize("outcome", ["iterable", "return_response", "raise_response"])
def test_blocked_iterator_is_closed_without_iteration(app, request_spans, close_fails, outcome):
    calls = []

    class Body:
        def __iter__(self):
            calls.append("iterated")
            return iter([b"must-not-return"])

        def close(self):
            calls.append("closed")
            if close_fails:
                raise RuntimeError("close failed")

    @app.route("/stream")
    def stream():
        headers = {"X-Test": "MagicKey_Al4h7iCFep9s1"}
        if outcome == "iterable":
            bottle.response.set_header("X-Test", headers["X-Test"])
            return Body()
        result = bottle.HTTPResponse(Body(), headers=headers)
        if outcome == "raise_response":
            raise result
        return result

    webtest.TestApp(app).get("/stream", status=403)
    assert calls == ["closed"]
    assert_span_http_status_code(request_spans[0], 403)


def test_unblocked_request_preserves_body_and_exception(app, request_spans):
    app.catchall = False
    failure = ValueError("callback failed")

    @app.post("/body")
    def body():
        assert bottle.request.body.read() == b"unchanged body"
        raise failure

    with pytest.raises(ValueError) as caught:
        webtest.TestApp(app).post("/body", params=b"unchanged body", content_type="text/plain")
    assert caught.value is failure
    assert get_active_asm_context() is None
    assert request_spans[0].finished
    assert request_spans[0].error == 1


@pytest.mark.skipif(sys.version_info < (3, 11), reason="Requires native exception groups")
def test_block_inside_exception_group_is_enforced(app, request_spans):
    @app.route("/group")
    def group():
        raise getattr(builtins, "BaseExceptionGroup")(
            "group", [ValueError("unrelated"), BlockingException(Block_config(status_code=429))]
        )

    webtest.TestApp(app).get("/group", status=429)
    assert_span_http_status_code(request_spans[0], 429)
    assert request_spans[0].error == 0
    assert get_active_asm_context() is None


@pytest.mark.parametrize("context_name", ["web.request", "bottle.request"])
@pytest.mark.parametrize("phase", ["request", "response"])
def test_shared_appsec_events_without_framework_objects(appsec, phase, context_name, mocker):
    cleanup = mocker.spy(web_framework_subscribers, "_on_context_ended")
    parent = get_active_asm_context()
    event = WebFrameworkRequestEvent(
        http_operation="test.request",
        component="test",
        integration_config=IntegrationConfig(Config(), "test"),
        request_method="GET",
        request_url="http://localhost/.git" if phase == "request" else "http://localhost/hello",
        request_headers={},
        headers_case_sensitive=False,
    )
    with core.context_with_event(event, context_name_override=context_name) as ctx:
        assert get_active_asm_context() is not None
        inspection = WebFrameworkRequestInspectEvent(
            request_event=event,
            peer_ip="127.0.0.1",
            cookies={"session": "example"},
            path_params={"name": "world"},
        )
        with pytest.raises(BlockingException) as blocked:
            core.dispatch_event(inspection)
            assert phase == "response"
            event.response_status_code = 200
            event.response_headers = {"X-Test": "MagicKey_Al4h7iCFep9s1"}
            core.dispatch_event(WebFrameworkResponseInspectEvent(event))
        replacement = WebFrameworkBlockResponseEvent(blocked.value.args[0])
        core.dispatch_event(replacement)
        assert replacement.response is not None
        status, headers, body = replacement.response
        assert status == 403
        assert int(headers["Content-Length"]) == len(body)
        event.response_status_code = status
        event.response_headers = headers
    assert get_active_asm_context() is parent
    cleanup.assert_called_once_with(ctx, (None, None, None))
    assert ctx.identifier == context_name
