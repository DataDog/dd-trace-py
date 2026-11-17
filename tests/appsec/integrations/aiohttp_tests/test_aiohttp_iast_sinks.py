import asyncio
import inspect
import logging
import traceback

from aiohttp import web
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict
import pytest

from ddtrace.appsec._iast import _aiohttp as iast_aiohttp
from ddtrace.appsec._iast._handlers import _on_aiohttp_request_start
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._patch_modules import _testing_unpatch_iast
from ddtrace.appsec._iast._taint_tracking import initialize_native_state
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from ddtrace.appsec._iast.constants import VULN_UNVALIDATED_REDIRECT
from ddtrace.appsec._iast.taint_sinks import insecure_cookie
from ddtrace.appsec._iast.taint_sinks import stacktrace_leak
from ddtrace.appsec._iast.taint_sinks import unvalidated_redirect
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from ddtrace.internal import core
from ddtrace.internal.security._patch_utils import try_unwrap
from tests.appsec.iast.iast_utils import IAST_VALID_LOG
from tests.appsec.iast.iast_utils import _iast_patched_module
from tests.appsec.iast.iast_utils import load_iast_report
from tests.utils import override_global_config
from tests.utils import scoped_tracer


TEST_FILE_PATH = "tests/appsec/integrations/aiohttp_tests/test_aiohttp_iast_sinks.py"
TARGET = "https://www.datadoghq.com/target"
COOKIE_VULNERABILITIES = {VULN_INSECURE_COOKIE, VULN_NO_HTTPONLY_COOKIE, VULN_NO_SAMESITE_COOKIE}
SSRF_HANDLERS_PATH = "tests/appsec/integrations/aiohttp_tests/ssrf_handlers.py"
SSRF_TARGET = "http://127.0.0.1:1/target"
TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/app/service/handlers.py", line 42, in process\n'
    "    result = compute(data)\n"
    "ValueError: invalid literal\n"
)


def _taint_request_sources(ctx, request, max_body_size):
    _on_aiohttp_request_start(ctx, request, max_body_size)


@pytest.fixture(scope="module", autouse=True)
def aiohttp_sinks():
    core.on("aiohttp.request.start", _taint_request_sources)
    with override_global_config(dict(_iast_enabled=True, _iast_is_testing=True)):
        insecure_cookie.patch()
        unvalidated_redirect.patch()
    yield
    core.reset_listeners("aiohttp.request.start", _taint_request_sources)
    with override_global_config(dict(_iast_is_testing=True)):
        _testing_unpatch_iast()
    for method in ("read", "text", "json", "post"):
        try_unwrap("aiohttp.web_request", f"BaseRequest.{method}")
    try_unwrap("aiohttp.web_request", "Request.clone")
    iast_aiohttp._sources_patched = False
    insecure_cookie._IS_PATCHED = False
    unvalidated_redirect._IS_PATCHED = False


@pytest.fixture(autouse=True)
def check_iast_logs(caplog):
    with override_global_config(dict(_iast_debug=True)), caplog.at_level(logging.DEBUG):
        yield
    for record in caplog.get_records("call"):
        if IAST_VALID_LOG.search(str(record.msg)):
            pytest.fail(str(record.msg))


def _next_line():
    return inspect.currentframe().f_back.f_lineno + 1


def _vulnerabilities(handler, method="GET", sub_app=False, **kwargs):
    spans = []
    completed = None

    async def traced(request):
        environment = _get_iast_env()
        assert environment is not None
        spans.append(environment.span)
        return await handler(request)

    def ended(ctx, exc_info):
        completed.put_nowait(ctx)

    async def run():
        nonlocal completed
        completed = asyncio.Queue()
        app = web.Application()
        target = web.Application() if sub_app else app
        target.router.add_route(method, "/sink", traced)
        if sub_app:
            trace_app(target)
            app.add_subapp("/sub", target)
        trace_app(app)
        async with TestClient(TestServer(app)) as client:
            path = "/sub/sink" if sub_app else "/sink"
            response = await client.request(method, path, allow_redirects=False, **kwargs)
            await response.read()
            for _ in range(2 if sub_app else 1):
                await asyncio.wait_for(completed.get(), timeout=5)
            return response

    core.on("context.ended.web.request", ended)
    try:
        with override_global_config(
            dict(_iast_enabled=True, _iast_request_sampling=100.0, _iast_deduplication_enabled=False)
        ):
            with scoped_tracer() as tracer:
                initialize_native_state()
                oce.reconfigure()
                tracer._recreate()
                response = asyncio.run(run())
    finally:
        oce.reconfigure()
        core.reset_listeners("context.ended.web.request", ended)
    assert len(spans) == 1
    assert spans[0].finished
    report = load_iast_report(spans[0])
    return response, report["vulnerabilities"] if report else []


def _assert_location(vulnerability, line):
    assert vulnerability["location"]["path"] == TEST_FILE_PATH
    assert vulnerability["location"]["line"] == line


@pytest.mark.parametrize(
    "flags,expected",
    [
        (dict(secure=False, httponly=False, samesite="None"), COOKIE_VULNERABILITIES),
        (dict(), {VULN_INSECURE_COOKIE, VULN_NO_HTTPONLY_COOKIE}),
        (dict(secure=True, httponly=False, samesite="Strict"), {VULN_NO_HTTPONLY_COOKIE}),
        (dict(secure=False, httponly=True, samesite="Lax"), {VULN_INSECURE_COOKIE}),
        (dict(secure=True, httponly=True, samesite="None"), {VULN_NO_SAMESITE_COOKIE}),
        (dict(secure=True, httponly=True, samesite="Strict"), set()),
        (dict(secure=True, httponly=True, samesite="lax"), set()),
    ],
)
def test_cookie_flags_are_reported(flags, expected):
    lines = []

    async def handler(request):
        response = web.Response(text="ok")
        lines.append(_next_line())
        response.set_cookie("session", "value", **flags)
        return response

    response, vulnerabilities = _vulnerabilities(handler)
    assert response.cookies["session"].value == "value"
    assert {vulnerability["type"] for vulnerability in vulnerabilities} == expected
    for vulnerability in vulnerabilities:
        assert vulnerability["evidence"] == {"valueParts": [{"value": "session"}]}
        _assert_location(vulnerability, lines[0])


def test_cookie_name_keyword_is_reported():
    async def handler(request):
        response = web.json_response({"ok": True})
        response.set_cookie(name="session", value="value", secure=True, httponly=True, samesite="None")
        return response

    _, vulnerabilities = _vulnerabilities(handler)
    assert [(vulnerability["type"], vulnerability["evidence"]) for vulnerability in vulnerabilities] == [
        (VULN_NO_SAMESITE_COOKIE, {"valueParts": [{"value": "session"}]})
    ]


def test_empty_cookie_is_not_reported():
    async def handler(request):
        response = web.Response(text="ok")
        response.set_cookie("session", "", secure=False, httponly=False, samesite="None")
        return response

    _, vulnerabilities = _vulnerabilities(handler)
    assert vulnerabilities == []


@pytest.mark.parametrize(
    "redirect,status",
    [
        (web.HTTPMultipleChoices, 300),
        (web.HTTPMovedPermanently, 301),
        (web.HTTPFound, 302),
        (web.HTTPSeeOther, 303),
        (web.HTTPTemporaryRedirect, 307),
        (web.HTTPPermanentRedirect, 308),
    ],
)
@pytest.mark.parametrize("keyword", [False, True])
def test_tainted_redirect_is_reported(redirect, status, keyword):
    lines = []

    async def handler(request):
        location = await request.text()
        lines.append(_next_line())
        raise redirect(location=location) if keyword else redirect(location)

    response, vulnerabilities = _vulnerabilities(handler, method="POST", data=TARGET)
    assert response.status == status
    assert response.headers["Location"] == TARGET
    assert [vulnerability["type"] for vulnerability in vulnerabilities] == [VULN_UNVALIDATED_REDIRECT]
    assert vulnerabilities[0]["evidence"]["valueParts"] == [{"value": TARGET, "source": 0}]
    _assert_location(vulnerabilities[0], lines[0])


@pytest.mark.parametrize("header", ["Location", "location"])
@pytest.mark.parametrize(
    "container",
    [dict, CIMultiDict, lambda headers: list(headers.items()), lambda headers: iter(headers.items())],
    ids=["dict", "CIMultiDict", "list", "iterator"],
)
@pytest.mark.parametrize("factory", ["response", "json_response", "stream_response"])
def test_tainted_location_header_is_reported(header, container, factory):
    lines = []

    async def handler(request):
        headers = container({header: await request.text()})
        if factory == "response":
            lines.append(_next_line())
            return web.Response(text="ok", status=302, headers=headers)
        if factory == "json_response":
            lines.append(_next_line())
            return web.json_response({"ok": True}, status=302, headers=headers)
        lines.append(_next_line())
        response = web.StreamResponse(status=302, headers=headers)
        await response.prepare(request)
        await response.write_eof()
        return response

    response, vulnerabilities = _vulnerabilities(handler, method="POST", data=TARGET)
    assert response.status == 302
    assert response.headers["Location"] == TARGET
    assert [vulnerability["type"] for vulnerability in vulnerabilities] == [VULN_UNVALIDATED_REDIRECT]
    _assert_location(vulnerabilities[0], lines[0])


def test_untainted_redirects_are_not_reported():
    async def handler(request):
        await request.text()
        if request.query.get("header"):
            return web.Response(text="ok", status=302, headers={"Location": TARGET})
        raise web.HTTPFound(TARGET)

    for params in ({}, {"header": "1"}):
        response, vulnerabilities = _vulnerabilities(handler, method="POST", data=TARGET, params=params)
        assert response.status == 302
        assert vulnerabilities == []


def test_tainted_header_other_than_location_is_not_a_redirect():
    async def handler(request):
        return web.Response(text="ok", headers={"X-Target": await request.text()})

    response, vulnerabilities = _vulnerabilities(handler, method="POST", data=TARGET)
    assert response.headers["X-Target"] == TARGET
    assert vulnerabilities == []


@pytest.mark.skipif(not iast_aiohttp.WRAPS_MAPPINGS, reason="multidict below 6.4 keeps aiohttp's mappings")
def test_redirect_to_a_request_header_is_not_reported():
    origins = []

    async def handler(request):
        location = request.headers["X-Target"]
        origins.append(origin_to_str(get_tainted_ranges(location)[0].source.origin))
        raise web.HTTPFound(location)

    response, vulnerabilities = _vulnerabilities(handler, headers={"X-Target": TARGET})
    assert response.headers["Location"] == TARGET
    assert origins == ["http.request.header"]
    assert vulnerabilities == []


def test_redirects_work_outside_a_request():
    response = web.HTTPFound(TARGET)
    assert response.headers["Location"] == TARGET
    assert web.Response(headers={"Location": TARGET}).headers["Location"] == TARGET


@pytest.fixture(scope="module")
def ssrf_handlers():
    return _iast_patched_module("tests.appsec.integrations.aiohttp_tests.ssrf_handlers")


def _line_of(handler, call):
    lines, start = inspect.getsourcelines(handler)
    return start + next(index for index, line in enumerate(lines) if call in line)


def _evidence_text(vulnerability):
    evidence = vulnerability["evidence"]
    return evidence.get("value") or "".join(part["value"] for part in evidence.get("valueParts", ()))


@pytest.mark.parametrize(
    "name,call",
    [
        ("session_get", "session.get(url)"),
        ("session_get_keyword", "session.get(url=url)"),
        ("session_post", "session.post(url"),
        ("session_put", "session.put(url)"),
        ("session_patch", "session.patch(url)"),
        ("session_delete", "session.delete(url)"),
        ("session_head", "session.head(url)"),
        ("session_options", "session.options(url)"),
        ("session_request", 'session.request("GET", url)'),
        ("session_request_keyword", 'session.request(method="GET", url=url)'),
        ("module_request", 'aiohttp.request("GET", url'),
        ("session_ws_connect", "session.ws_connect(url)"),
    ],
)
def test_tainted_client_url_is_reported(ssrf_handlers, name, call):
    handler = getattr(ssrf_handlers, name)
    response, vulnerabilities = _vulnerabilities(handler, method="POST", data=SSRF_TARGET)
    assert response.status == 200
    assert [vulnerability["type"] for vulnerability in vulnerabilities] == [VULN_SSRF]
    assert vulnerabilities[0]["evidence"]["valueParts"] == [{"value": SSRF_TARGET, "source": 0}]
    assert vulnerabilities[0]["location"]["path"] == SSRF_HANDLERS_PATH
    assert vulnerabilities[0]["location"]["line"] == _line_of(handler, call)


def test_client_url_credentials_are_redacted(ssrf_handlers):
    handler = ssrf_handlers.session_ws_connect_with_credentials
    response, vulnerabilities = _vulnerabilities(handler, method="POST", data="room")
    assert response.status == 200
    assert [vulnerability["type"] for vulnerability in vulnerabilities] == [VULN_SSRF]
    assert vulnerabilities[0]["evidence"]["valueParts"] == [
        {"value": "ws://"},
        {"redacted": True},
        {"value": "@127.0.0.1:1/"},
        {"value": "room", "source": 0},
    ]


def test_untainted_client_url_is_not_reported(ssrf_handlers):
    response, vulnerabilities = _vulnerabilities(ssrf_handlers.session_get_untainted, method="POST", data=SSRF_TARGET)
    assert response.status == 200
    assert vulnerabilities == []


def test_client_url_tainted_only_in_the_fragment_is_not_reported(ssrf_handlers):
    ssrf_handlers.URLS.clear()
    response, vulnerabilities = _vulnerabilities(
        ssrf_handlers.session_get_tainted_fragment, method="POST", data="fragment"
    )
    assert response.status == 200
    assert vulnerabilities == []
    [(url, starts)] = ssrf_handlers.URLS
    assert starts
    assert all(start > url.index("#") for start in starts)


def _current_traceback():
    try:
        int("invalid literal")
    except ValueError:
        return traceback.format_exc()


@pytest.mark.parametrize("form", ["text", "body", "raised", "format_exc"])
def test_stacktrace_in_response_body_is_reported(form):
    async def handler(request):
        if form == "raised":
            raise web.HTTPInternalServerError(text=TRACEBACK)
        if form == "body":
            return web.Response(body=TRACEBACK.encode(), status=500)
        if form == "format_exc":
            return web.Response(text=_current_traceback(), status=500)
        return web.Response(text=TRACEBACK, status=500)

    response, vulnerabilities = _vulnerabilities(handler)
    assert response.status == 500
    assert [vulnerability["type"] for vulnerability in vulnerabilities] == [VULN_STACKTRACE_LEAK]
    assert "Exception: ValueError" in _evidence_text(vulnerabilities[0])


@pytest.mark.parametrize("method,status", [("HEAD", 500), ("GET", 204), ("GET", 304)])
def test_stacktrace_in_a_body_that_is_not_sent_is_not_reported(method, status):
    async def handler(request):
        return web.Response(text=TRACEBACK, status=status)

    response, vulnerabilities = _vulnerabilities(handler, method=method)
    assert response.status == status
    assert vulnerabilities == []


def test_traced_sub_app_checks_the_response_once(monkeypatch):
    checks = []
    original = stacktrace_leak.iast_check_stacktrace_leak

    def check(content):
        checks.append(content)
        return original(content)

    monkeypatch.setattr(stacktrace_leak, "iast_check_stacktrace_leak", check)

    async def handler(request):
        return web.Response(text=TRACEBACK, status=500)

    response, vulnerabilities = _vulnerabilities(handler, sub_app=True)
    assert response.status == 500
    assert [vulnerability["type"] for vulnerability in vulnerabilities] == [VULN_STACKTRACE_LEAK]
    assert checks == [TRACEBACK]


@pytest.mark.parametrize(
    "text",
    ["Traceback is a word, not a stack trace", "Traceback (most recent call last):\nnothing else"],
)
def test_response_without_stacktrace_is_not_reported(text):
    async def handler(request):
        return web.Response(text=text, status=500)

    response, vulnerabilities = _vulnerabilities(handler)
    assert response.status == 500
    assert vulnerabilities == []
