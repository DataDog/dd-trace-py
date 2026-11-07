import asyncio
from collections import defaultdict
import json
import logging
import os
from types import MappingProxyType
from unittest import mock

from aiohttp import FormData
from aiohttp import web
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict
from multidict import CIMultiDictProxy
from multidict import MultiDictProxy
import pytest

from ddtrace.appsec._asm_request_context import iast_disabled_taint_sources
from ddtrace.appsec._iast import _aiohttp as iast_aiohttp
from ddtrace.appsec._iast._handlers import _on_aiohttp_request_start
from ddtrace.appsec._iast._iast_env import _get_iast_env
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import initialize_native_state
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from ddtrace.internal import core
from ddtrace.internal.security._patch_utils import try_unwrap
from tests.appsec.iast.iast_utils import IAST_VALID_LOG
from tests.utils import override_global_config
from tests.utils import scoped_tracer


PARAMETER = "http.request.parameter"
PARAMETER_NAME = "http.request.parameter.name"
HEADER = "http.request.header"
HEADER_NAME = "http.request.header.name"
COOKIE = "http.request.cookie.value"
COOKIE_NAME = "http.request.cookie.name"
BODY = "http.request.body"
PATH = "http.request.path"
QUERY = origin_to_str(OriginType.QUERY)

wraps_mappings = pytest.mark.skipif(
    not iast_aiohttp.WRAPS_MAPPINGS, reason="multidict below 6.4 keeps aiohttp's mappings"
)


@pytest.fixture(scope="module", autouse=True)
def aiohttp_sources():
    core.on("aiohttp.request.start", _on_aiohttp_request_start)
    yield
    core.reset_listeners("aiohttp.request.start", _on_aiohttp_request_start)
    for method in ("read", "text", "json", "post"):
        try_unwrap("aiohttp.web_request", f"BaseRequest.{method}")
    iast_aiohttp._sources_patched = False


@pytest.fixture(autouse=True)
def check_iast_logs(caplog):
    with override_global_config(dict(_iast_debug=True)), caplog.at_level(logging.DEBUG):
        yield
    for record in caplog.get_records("call"):
        if IAST_VALID_LOG.search(str(record.msg)):
            pytest.fail(str(record.msg))


def _origin(value):
    ranges = get_tainted_ranges(value)
    return origin_to_str(ranges[0].source.origin) if ranges else None


def _call(
    handler,
    path,
    method="GET",
    route="/sources/{table}",
    sampling=100.0,
    sub_app=False,
    parent_middlewares=(),
    **kwargs,
):
    async def run():
        app = handler_app = web.Application(middlewares=list(parent_middlewares))
        if sub_app:
            handler_app = web.Application()
            trace_app(handler_app)
        handler_app.router.add_route("*", route, handler)
        if sub_app:
            app.add_subapp("/sub", handler_app)
        trace_app(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.request(method, path, **kwargs)
            assert response.status == 200, await response.text()
            return await response.json()

    try:
        with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=sampling)):
            with scoped_tracer() as tracer:
                initialize_native_state()
                oce.reconfigure()
                tracer._recreate()
                return asyncio.run(run())
    finally:
        oce.reconfigure()


async def _request_sources(request):
    query_name, query_value = list(request.query.items())[0]
    return web.json_response(
        {
            "query": _origin(request.query.get("table")),
            "query item": _origin(request.query["table"]),
            "query getone": _origin(request.query.getone("table")),
            "query getall": [_origin(value) for value in request.query.getall("table")],
            "query name": _origin(next(iter(request.query))),
            "query items": [_origin(query_name), _origin(query_value)],
            "query dict": _origin(dict(request.query)["table"]),
            "header": _origin(request.headers.get("x-table")),
            "header name": _origin(next(key for key in request.headers if key.lower() == "x-table")),
            "cookie header": _origin(request.headers["cookie"]),
            "cookie": _origin(request.cookies.get("table")),
            "cookie name": _origin(next(iter(request.cookies))),
            "path": _origin(request.path),
            "path parameter": _origin(request.match_info["table"]),
            "missing query": request.query.get("missing", "default"),
        }
    )


@wraps_mappings
def test_request_sources_are_tainted():
    result = _call(
        _request_sources,
        "/sources/users?table=users&table=admins",
        headers={"X-Table": "users", "Cookie": "table=users"},
    )
    assert result == {
        "query": PARAMETER,
        "query item": PARAMETER,
        "query getone": PARAMETER,
        "query getall": [PARAMETER, PARAMETER],
        "query name": PARAMETER_NAME,
        "query items": [PARAMETER_NAME, PARAMETER],
        "query dict": PARAMETER,
        "header": HEADER,
        "header name": HEADER_NAME,
        "cookie header": COOKIE,
        "cookie": COOKIE,
        "cookie name": COOKIE_NAME,
        "path": "http.request.path",
        "path parameter": "http.request.path.parameter",
        "missing query": "default",
    }


async def _body_sources(request):
    kind = request.match_info["table"]
    if kind == "json":
        return web.json_response({"value": _origin((await request.json())["table"])})
    if kind == "suppressed-json":
        with iast_disabled_taint_sources():
            body = await request.json()
        return web.json_response({"value": _origin(body["table"])})
    if kind == "json-custom-loader":
        seen = {}

        def loader(text):
            seen["input"] = _origin(text)
            return json.loads(text)

        body = await request.json(loads=loader)
        return web.json_response({"value": _origin(body["table"]), "loader input": seen["input"]})
    if kind == "json-string":
        return web.json_response({"value": _origin(await request.json())})
    if kind == "text":
        return web.json_response({"value": _origin(await request.text())})
    if kind == "suppressed-then-text":
        with iast_disabled_taint_sources():
            await request.text()
        return web.json_response({"value": _origin(await request.text())})
    if kind == "text-then-suppressed":
        await request.text()
        with iast_disabled_taint_sources():
            return web.json_response({"value": _origin(await request.text())})
    if kind == "text-twice":
        await request.text()
        return web.json_response({"value": _origin(await request.text())})
    if kind == "read":
        return web.json_response({"value": _origin(await request.read())})
    form = await request.post()
    return web.json_response(
        {
            "value": _origin(form.get("table")),
            "name": _origin(next(iter(form))),
            "files": len(form.getall("upload", [])),
        }
    )


def _multipart():
    form = FormData()
    form.add_field("table", "users")
    form.add_field("upload", b"content", filename="upload.txt")
    return form


@pytest.mark.parametrize(
    ("kind", "kwargs", "expected"),
    [
        ("json", {"json": {"table": "users"}}, {"value": BODY}),
        ("json-string", {"json": "users"}, {"value": BODY}),
        ("json-custom-loader", {"json": {"table": "users"}}, {"value": BODY, "loader input": BODY}),
        ("suppressed-json", {"json": {"table": "users"}}, {"value": None}),
        ("text", {"data": "table=users"}, {"value": BODY}),
        ("read", {"data": b"table=users"}, {"value": BODY}),
        ("suppressed-then-text", {"data": "table=users"}, {"value": BODY}),
        ("text-then-suppressed", {"data": "table=users"}, {"value": None}),
        pytest.param(
            "form",
            {"data": {"table": "users"}},
            {"value": BODY, "name": PARAMETER_NAME, "files": 0},
            marks=wraps_mappings,
        ),
        pytest.param(
            "multipart", {"data": _multipart}, {"value": BODY, "name": PARAMETER_NAME, "files": 1}, marks=wraps_mappings
        ),
    ],
)
def test_body_sources_are_tainted(kind, kwargs, expected):
    kwargs = {key: value() if callable(value) else value for key, value in kwargs.items()}
    assert _call(_body_sources, f"/sources/{kind}", method="POST", **kwargs) == expected


async def _unsampled_sources(request):
    return web.json_response(
        {
            "headers": type(request.headers).__name__,
            "query": _origin(request.query.get("table")),
            "header": _origin(request.headers.get("x-table")),
            "path parameter": _origin(request.match_info["table"]),
            "body": _origin((await request.post()).get("table")),
        }
    )


def test_sources_are_not_tainted_when_the_request_is_not_sampled():
    result = _call(
        _unsampled_sources,
        "/sources/users?table=users",
        method="POST",
        sampling=0.0,
        headers={"X-Table": "users"},
        data={"table": "users"},
    )
    assert result == {
        "headers": "CIMultiDictProxy",
        "query": None,
        "header": None,
        "path parameter": None,
        "body": None,
    }


async def _suppressed_then_read(request):
    with iast_disabled_taint_sources():
        suppressed = _origin(request.headers.get("x-table"))
    first = request.headers.get("x-table")
    return web.json_response(
        {
            "suppressed": suppressed,
            "after": _origin(first),
            "same object": first is request.headers.get("x-table"),
            "endpoint": [_get_iast_env().endpoint_method, _get_iast_env().endpoint_route],
        }
    )


@wraps_mappings
def test_suppressed_reads_are_tainted_on_the_next_read():
    result = _call(_suppressed_then_read, "/sources/users", headers={"X-Table": "users"})
    assert result == {
        "suppressed": None,
        "after": HEADER,
        "same object": True,
        "endpoint": ["GET", "/sources/{table}"],
    }


def _reversed_error(mapping):
    try:
        reversed(mapping)
    except TypeError:
        return "TypeError"
    return "reversible"


async def _mapping_behavior(request):
    form = await request.post()
    header_name, header_value = next((k, v) for k, v in request.headers.items() if k.lower() == "x-table")
    return web.json_response(
        {
            "getone default": request.query.getone("missing", default="d"),
            "getall default": request.query.getall("missing", default=[]),
            "key in keys": "x-table" in request.headers.keys(),
            "item in items": ("x-table", "users") in request.headers.items(),
            "value in values": "users" in request.headers.values(),
            "keys and set": sorted(request.headers.keys() & {"x-table", "missing"}),
            "keys and set origin": _origin(next(iter(request.headers.keys() & {"x-table"}))),
            "keys minus set": [k for k in request.headers.keys() - {"x-table"} if k.lower() == "x-table"],
            "items and set": sorted(request.headers.items() & {("x-table", "users")}),
            "allowlist and keys origin": _origin(next(iter({"x-table"} & request.headers.keys()))),
            "exact case and origin": _origin(next(iter(request.headers.keys() & {"X-Table"}))),
            "dict keys and origin": _origin(next(iter({"X-Table": 1}.keys() & request.headers.keys()))),
            "keys or origins": sorted(
                [k, _origin(k)] for k in request.headers.keys() | {"zz-other"} if k.lower() in ("x-table", "zz-other")
            ),
            "reflected or origins": sorted(
                [k, _origin(k)] for k in {"zz-other"} | request.headers.keys() if k.lower() in ("x-table", "zz-other")
            ),
            "reflected minus": sorted({"x-table", "zz-other"} - request.headers.keys()),
            "query or headers origins": sorted(
                [k, _origin(k)] for k in request.query.keys() | request.headers.keys() if k in ("table", "User-Agent")
            ),
            "headers or query items origin": [
                _origin(k) for k, v in request.headers.items() | request.query.items() if k == "table"
            ],
            "app multidict keys origin": [
                _origin(k) for k in request.headers.keys() | CIMultiDict([("X-Extra", "app")]).keys() if k == "X-Extra"
            ],
            "query and headers like native": sorted(request.query.keys() & request.headers.keys())
            == sorted(request.query.copy().keys() & CIMultiDict(request.headers).keys()),
            "keys xor origins": sorted(
                [k, _origin(k)] for k in request.headers.keys() ^ {"zz-other"} if k.lower() in ("x-table", "zz-other")
            ),
            "reflected xor origins": sorted(
                [k, _origin(k)] for k in {"zz-other"} ^ request.headers.keys() if k.lower() in ("x-table", "zz-other")
            ),
            "items or app pair origin": [
                _origin(k) for k, v in request.headers.items() | {"X-Extra": "app"}.items() if k == "X-Extra"
            ],
            "items or non pair": sorted(str(e) for e in request.headers.items() | {1: "a"}.items() if e[0] == 1),
            "items or multidict pair origin": [
                _origin(k)
                for k, v in request.headers.items() | CIMultiDict([("X-Extra", "app")]).items()
                if k == "X-Extra"
            ],
            "items xor multidict pair origin": [
                _origin(k)
                for k, v in request.headers.items() ^ CIMultiDict([("X-Extra", "app")]).items()
                if k == "X-Extra"
            ],
            "reversed like native": _reversed_error(request.headers)
            == _reversed_error(CIMultiDictProxy(CIMultiDict(request.headers))),
            "allowlist and items origin": _origin(next(iter({("x-table", "users")} & request.headers.items()))[0]),
            "keys equal list": request.headers.keys() == list(CIMultiDict(request.headers).keys()),
            "native keys equal list": CIMultiDict(request.headers).keys() == list(CIMultiDict(request.headers).keys()),
            "headers repr": repr(request.headers).startswith("<CIMultiDictProxy("),
            "query repr": repr(request.query).startswith("<MultiDictProxy("),
            "header item": [_origin(header_name), _origin(header_value)],
            "query value": _origin(next(iter(request.query.values()))),
            "same header": request.headers.get("X-Table") is request.headers.get("x-table"),
            "query copy": [_origin(next(iter(request.query.copy()))), _origin(request.query.copy()["table"])],
            "cookies copy": _origin(request.cookies.copy()["table"]),
            "cookies or": _origin((request.cookies | {"other": "x"})["table"]),
            "cookies reversed": _origin(next(reversed(request.cookies))),
            "cookie views reversed": [
                _origin(next(reversed(request.cookies.keys()))),
                [_origin(part) for part in next(reversed(request.cookies.items()))],
                _origin(next(reversed(request.cookies.values()))),
            ],
            "cookies or defaultdict": [
                type(request.cookies | defaultdict(str)).__name__,
                _origin((request.cookies | defaultdict(str))["table"]),
                type(defaultdict(str) | request.cookies).__name__,
                _origin((defaultdict(str) | request.cookies)["table"]),
            ],
            "cookies repr": repr(request.cookies),
            "same form": form is await request.post(),
        }
    )


@wraps_mappings
def test_tainted_mappings_behave_like_aiohttp_mappings():
    result = _call(
        _mapping_behavior,
        "/sources/users?table=users&x-table=1",
        method="POST",
        headers={"X-Table": "users", "Cookie": "table=users"},
        data={"table": "users"},
    )
    assert result == {
        "getone default": "d",
        "getall default": [],
        "key in keys": True,
        "item in items": True,
        "value in values": True,
        "keys and set": ["X-Table"],
        "keys and set origin": HEADER_NAME,
        "keys minus set": [],
        "items and set": [["X-Table", "users"]],
        "allowlist and keys origin": None,
        "exact case and origin": HEADER_NAME,
        "dict keys and origin": HEADER_NAME,
        "keys or origins": [["X-Table", HEADER_NAME], ["zz-other", None]],
        "reflected or origins": [["X-Table", HEADER_NAME], ["zz-other", None]],
        "reflected minus": ["zz-other"],
        "query or headers origins": [["User-Agent", HEADER_NAME], ["table", PARAMETER_NAME]],
        "headers or query items origin": [PARAMETER_NAME],
        "app multidict keys origin": [None],
        "query and headers like native": True,
        "keys xor origins": [["X-Table", HEADER_NAME], ["zz-other", None]],
        "reflected xor origins": [["X-Table", HEADER_NAME], ["zz-other", None]],
        "items or app pair origin": [None],
        "items or non pair": ["(1, 'a')"],
        "items or multidict pair origin": [None],
        "items xor multidict pair origin": [None],
        "reversed like native": True,
        "allowlist and items origin": None,
        "keys equal list": result["native keys equal list"],
        "native keys equal list": result["native keys equal list"],
        "headers repr": True,
        "query repr": True,
        "header item": [HEADER_NAME, HEADER],
        "query value": PARAMETER,
        "same header": True,
        "query copy": [PARAMETER_NAME, PARAMETER],
        "cookies copy": COOKIE,
        "cookies or": COOKIE,
        "cookies reversed": COOKIE_NAME,
        "cookie views reversed": [COOKIE_NAME, [COOKIE_NAME, COOKIE], COOKIE],
        "cookies or defaultdict": ["defaultdict", COOKIE, "defaultdict", COOKIE],
        "cookies repr": "mappingproxy({'table': 'users'})",
        "same form": True,
    }


@pytest.mark.parametrize(
    ("kind", "kwargs", "body_taints"),
    [
        ("json", {"json": {"table": "users"}}, 0),
        ("text", {"data": "table=users"}, 1),
        ("text-twice", {"data": "table=users"}, 1),
        pytest.param("form", {"data": {"table": "users"}}, 1, marks=wraps_mappings),
    ],
)
def test_body_is_tainted_by_the_outermost_read_only(kind, kwargs, body_taints):
    with mock.patch.object(iast_aiohttp, "_taint_source", wraps=iast_aiohttp._taint_source) as taint_source:
        _call(_body_sources, f"/sources/{kind}", method="POST", **kwargs)
    assert sum(call.args[2] == OriginType.BODY for call in taint_source.call_args_list) == body_taints


_DDTRACE_RUN_APP = """
import asyncio
import json

from aiohttp import web
from aiohttp.test_utils import TestClient
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict

from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects_base import get_tainted_ranges
from ddtrace.contrib.aiohttp import trace_app


def origin(value):
    ranges = get_tainted_ranges(value)
    return origin_to_str(ranges[0].source.origin) if ranges else None


async def handler(request):
    form = await request.post()
    return web.json_response(
        {
            "query": origin(request.query.get("table")),
            "header": origin(request.headers.get("x-table")),
            "form": origin(form.get("table")),
            "missing header": request.headers.get("x-missing", "default"),
            "items or": "a" in (request.headers.items() | {"a"}),
            "query or headers": origin(
                next(k for k in request.query.keys() | request.headers.keys() if k == "X-Table")
            ),
            "app keys or": origin(
                next(k for k in request.headers.keys() | CIMultiDict([("X-Extra", "app")]).keys() if k == "X-Extra")
            ),
        }
    )


async def main():
    app = web.Application()
    app.router.add_post("/", handler)
    trace_app(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/?table=users", data={"table": "users"}, headers={"X-Table": "users"})
        print(json.dumps(await response.json()))


asyncio.run(main())
"""


@pytest.mark.parametrize(
    ("aiohttp_integration", "multidict_extensions"),
    [
        pytest.param("true", True, marks=wraps_mappings),
        pytest.param("false", True, marks=wraps_mappings),
        pytest.param("true", False, id="pure-python-multidict"),
    ],
)
def test_sources_under_ddtrace_run(python_process, aiohttp_integration, multidict_extensions):
    env = os.environ.copy()
    env.update(
        {
            "DD_IAST_ENABLED": "true",
            "DD_IAST_REQUEST_SAMPLING": "100",
            "DD_TRACE_AIOHTTP_ENABLED": aiohttp_integration,
            "DD_TRACE_AGENT_URL": "http://127.0.0.1:1",
            "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "false",
            "DD_REMOTE_CONFIGURATION_ENABLED": "false",
        }
    )
    if not multidict_extensions:
        env["MULTIDICT_NO_EXTENSIONS"] = "1"
    out, err, status, _ = python_process(_DDTRACE_RUN_APP, env=env, timeout=60, ddtrace_run=True)
    assert status == 0, err.decode()
    assert json.loads(out.decode().splitlines()[-1]) == {
        "query": PARAMETER,
        "header": HEADER,
        "form": BODY,
        "missing header": "default",
        "items or": True,
        "query or headers": HEADER_NAME,
        "app keys or": None,
    }


def _reversed(view):
    try:
        return list(reversed(view))
    except TypeError:
        return None


async def _application_views(request):
    native = CIMultiDict(request.headers)
    reversed_items = _reversed(request.headers.items())
    CIMultiDictProxy(request.headers)
    MultiDictProxy(request.query)
    MultiDictProxy(await request.post())
    return web.json_response(
        {
            "reversed like native": reversed_items == _reversed(native.items()),
            "reversed origin": next((_origin(v) for k, v in reversed_items if k.lower() == "x-table"), None)
            if reversed_items
            else None,
            "reversible": reversed_items is not None,
            "native headers": type(request.headers) is CIMultiDictProxy,
            "query": _origin(request.query.get("table")),
            "header": _origin(request.headers.get("x-table")),
            "path": _origin(request.path),
        }
    )


@pytest.mark.parametrize("sub_app", [False, True])
def test_request_mappings_work_with_application_proxies(sub_app):
    result = _call(
        _application_views,
        f"{'/sub' if sub_app else ''}/sources/users?table=users",
        method="POST",
        sub_app=sub_app,
        headers={"X-Table": "users", "Cookie": "a,b=c"},
        data={"table": "users"},
    )
    wrapped = iast_aiohttp.WRAPS_MAPPINGS
    assert result == {
        "reversed like native": True,
        "reversed origin": HEADER if wrapped and result["reversible"] else None,
        "reversible": result["reversible"],
        "native headers": not wrapped,
        "query": PARAMETER if wrapped else None,
        "header": HEADER if wrapped else None,
        "path": PATH,
    }


async def _unwrapped_sources(request):
    return web.json_response(
        {
            "cookie": _origin(request.cookies.get("table")),
            "cookie name": _origin(next(iter(request.cookies))),
            "path": _origin(request.path),
            "path parameter": _origin(request.match_info["table"]),
            "query string": _origin(request.query_string),
            "body": _origin(await request.text()),
        }
    )


def test_cookie_path_and_body_sources_on_every_multidict():
    result = _call(
        _unwrapped_sources, "/sources/users?table=users", method="POST", headers={"Cookie": "table=users"}, data="body"
    )
    assert result == {
        "cookie": COOKIE,
        "cookie name": COOKIE_NAME,
        "path": PATH,
        "path parameter": "http.request.path.parameter",
        "query string": QUERY,
        "body": BODY,
    }


@pytest.mark.parametrize("wraps", [True, False])
def test_instrumented_origins_follow_mapping_support(wraps):
    with mock.patch.object(iast_aiohttp, "WRAPS_MAPPINGS", wraps):
        origins = iast_aiohttp.instrumented_origins()
    assert (OriginType.HEADER in origins, OriginType.PARAMETER_NAME in origins) == (wraps, wraps)
    assert OriginType.COOKIE in origins and OriginType.BODY in origins and OriginType.QUERY in origins


class _AppObjectsNative:
    """Mimics pure-Python multidict below 6.4, whose & returns the application's own objects."""

    def __and__(self, other):
        return set(other)

    def __contains__(self, element):
        return True


class _TaintNames:
    def _taint_name(self, name):
        return ("tainted", name)


def test_set_operations_leave_the_application_objects_untainted():
    view = iast_aiohttp._TaintedKeysView(_TaintNames(), _AppObjectsNative())
    allowed = {"table"}
    assert view & allowed == {"table"}


class _CaseInsensitiveKeys:
    """A header key view whose union keeps both spellings, as some multidict builds do."""

    def __init__(self, keys):
        self._keys = keys

    def __iter__(self):
        return iter(self._keys)

    def __contains__(self, key):
        return key.lower() in {k.lower() for k in self._keys}

    def __or__(self, other):
        return set(self._keys) | set(other)


class _LabelledNames:
    def __init__(self, label):
        self._label = label

    def _taint_name(self, name):
        return (self._label, name)


def test_request_set_operations_keep_each_mapping_origin():
    headers = iast_aiohttp._TaintedKeysView(_LabelledNames("header"), _CaseInsensitiveKeys(["X-Table"]))
    query = iast_aiohttp._TaintedKeysView(_LabelledNames("query"), ["x-table"])
    assert headers | query == {("header", "X-Table"), ("query", "x-table")}


async def _read_one_header(request):
    return web.json_response({"header": _origin(request.headers.get("x-table"))})


@wraps_mappings
def test_traced_sub_app_only_taints_what_the_handler_reads():
    with mock.patch.object(iast_aiohttp, "_taint_source", wraps=iast_aiohttp._taint_source) as taint_source:
        result = _call(_read_one_header, "/sub/sources/users", sub_app=True, headers={"X-Table": "users"})
    assert result == {"header": HEADER}
    origins = {(call.args[1], call.args[2]) for call in taint_source.call_args_list}
    assert ("x-table", OriginType.HEADER) in origins
    assert not any(origin == OriginType.HEADER_NAME for _, origin in origins)


async def _cloned_request_sources(request):
    clone = request.clone()
    replaced = request.clone(headers={"X-Table": "replaced"})
    rewritten = request.clone(rel_url="/internal?table=internal")
    replaced_then_rewritten = request.clone(headers={"X-Table": "app-value"}).clone(rel_url="/rewritten")
    rewritten_then_replaced = request.clone(rel_url="/app/path?x=1").clone(headers={"X-Table": "app-value"})
    return web.json_response(
        {
            "query": _origin(clone.query.get("table")),
            "header": _origin(clone.headers.get("x-table")),
            "replaced header": _origin(replaced.headers.get("x-table")),
            "replaced url": [
                _origin(rewritten.path),
                _origin(rewritten.query_string),
                _origin(rewritten.query.get("table")),
            ],
            "replaced url header": _origin(rewritten.headers.get("x-table")),
            "chained header": _origin(replaced_then_rewritten.headers.get("x-table")),
            "chained url": [_origin(rewritten_then_replaced.path), _origin(rewritten_then_replaced.query_string)],
            "query string": _origin(clone.query_string),
            "path": _origin(clone.path),
            "path parameter": _origin(clone.match_info["table"]),
        }
    )


def test_cloned_requests_keep_their_sources():
    result = _call(_cloned_request_sources, "/sources/users?table=users", headers={"X-Table": "users"})
    wrapped = iast_aiohttp.WRAPS_MAPPINGS
    assert result == {
        "query": PARAMETER if wrapped else None,
        "header": HEADER if wrapped else None,
        "replaced header": None,
        "replaced url": [None, None, None],
        "replaced url header": HEADER if wrapped else None,
        "chained header": None,
        "chained url": [None, None],
        "query string": QUERY,
        "path": PATH,
        "path parameter": "http.request.path.parameter",
    }


async def _no_query(request):
    return web.json_response({"path": _origin(request.path)})


def test_empty_values_are_not_counted_as_sources():
    with mock.patch.object(iast_aiohttp, "taint_pyobject", wraps=iast_aiohttp.taint_pyobject) as taint_pyobject:
        result = _call(_no_query, "/sources/users")
    assert result == {"path": PATH}
    assert not any(call.kwargs.get("source_origin") == OriginType.QUERY for call in taint_pyobject.call_args_list)


@web.middleware
async def _replace_headers(request, handler):
    return await handler(request.clone(headers={"X-Table": "app-value"}))


async def _header_and_path(request):
    return web.json_response({"header": _origin(request.headers.get("x-table")), "path": _origin(request.path)})


def test_parent_app_clone_keeps_replaced_headers_untainted():
    result = _call(
        _header_and_path,
        "/sub/sources/users",
        sub_app=True,
        parent_middlewares=[_replace_headers],
        headers={"X-Table": "users"},
    )
    assert result == {"header": None, "path": PATH}


def test_read_only_match_info_is_left_untouched():
    request = mock.Mock(_cache={}, match_info=MappingProxyType({"table": "users"}))
    iast_aiohttp._wrap_request(request, headers=False, url=False)
    assert request.match_info == {"table": "users"}
