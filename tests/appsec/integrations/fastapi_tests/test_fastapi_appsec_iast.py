import io
import json
import logging
import sys
import typing

from fastapi import Cookie
from fastapi import Form
from fastapi import Header
from fastapi import Request
from fastapi import UploadFile
from fastapi import __version__ as _fastapi_version
from fastapi.responses import JSONResponse
import pytest
from starlette.responses import PlainTextResponse

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._handlers import _on_iast_fastapi_patch
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._patch_modules import patch_iast
from ddtrace.appsec._iast._taint_tracking import origin_to_str
from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from ddtrace.appsec._iast.constants import VULN_XSS
from ddtrace.appsec._iast.taint_sinks.header_injection import patch as patch_header_injection
from ddtrace.appsec._iast.taint_sinks.insecure_cookie import patch as patch_insecure_cookie
from ddtrace.contrib.internal.fastapi.patch import patch as patch_fastapi
from ddtrace.contrib.internal.sqlite3.patch import patch as patch_sqlite_sqli
from tests.appsec.iast.iast_utils import IAST_VALID_LOG
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.appsec.iast.taint_sinks.test_stacktrace_leak import _load_text_stacktrace
from tests.utils import override_env
from tests.utils import override_global_config


TEST_FILE_PATH = "tests/appsec/integrations/fastapi_tests/test_fastapi_appsec_iast.py"

fastapi_version = tuple([int(v) for v in _fastapi_version.split(".")])


def _aux_appsec_prepare_tracer(tracer):
    _on_iast_fastapi_patch()
    patch_fastapi()
    patch_sqlite_sqli()
    patch_header_injection()
    patch_insecure_cookie()
    oce.reconfigure()

    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer._recreate()


def get_response_body(response):
    return response.text


@pytest.fixture(autouse=True)
def check_native_code_exception_in_each_fastapi_test(request, caplog, telemetry_writer):
    if "skip_iast_check_logs" in request.keywords:
        yield
    else:
        caplog.set_level(logging.DEBUG)
        with override_env({"_DD_IAST_USE_ROOT_SPAN": "false"}), override_global_config(
            dict(_iast_debug=True)
        ), caplog.at_level(logging.DEBUG):
            yield

        log_messages = [record.msg for record in caplog.get_records("call")]
        for message in log_messages:
            if IAST_VALID_LOG.search(message):
                pytest.fail(message)
        list_metrics_logs = list(telemetry_writer._logs)
        assert len(list_metrics_logs) == 0


def test_query_param_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
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

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
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


def test_query_param_name_source_get(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
        query_params = [k for k in request.query_params.keys() if k == "iast_queryparam"][0]
        ranges_result = get_tainted_ranges(query_params)

        return JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
                "ranges_origin_name": ranges_result[0].source.name,
                "ranges_origin_value": ranges_result[0].source.value,
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html?iast_queryparam=test1234",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "iast_queryparam"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 15
        assert result["ranges_origin"] == "http.request.parameter.name"
        assert result["ranges_origin_name"] == "iast_queryparam"
        assert result["ranges_origin_value"] == "iast_queryparam"


def test_query_param_name_source_post(fastapi_application, client, tracer, test_spans):
    @fastapi_application.post("/index.html")
    async def test_route(request: Request):
        form_data = await request.form()
        query_params = [k for k in form_data.keys() if k == "iast_queryparam"][0]
        ranges_result = get_tainted_ranges(query_params)

        return JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
                "ranges_origin_name": ranges_result[0].source.name,
                "ranges_origin_value": ranges_result[0].source.value,
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post(
            "/index.html",
            data={"iast_queryparam": "test1234"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "iast_queryparam"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 15
        assert result["ranges_origin"] == "http.request.parameter.name"
        assert result["ranges_origin_name"] == "iast_queryparam"
        assert result["ranges_origin_value"] == "iast_queryparam"


def test_header_value_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
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

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
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


def test_header_name_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
        query_params = [k for k in request.headers.keys() if k == "iast_header"][0]
        ranges_result = get_tainted_ranges(query_params)

        return JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
                "ranges_origin_name": ranges_result[0].source.name,
                "ranges_origin_value": ranges_result[0].source.value,
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html",
            headers={"iast_header": "test1234"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "iast_header"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 11
        assert result["ranges_origin"] == "http.request.header.name"
        assert result["ranges_origin_name"] == "iast_header"
        assert result["ranges_origin_value"] == "iast_header"


@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated was introduced on 3.9")
@pytest.mark.skipif(fastapi_version < (0, 95, 0), reason="Header annotation doesn't work on fastapi 94 or lower")
def test_header_value_source_typing_param(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(iast_header: typing.Annotated[str, Header()] = None):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

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

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
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


def test_cookies_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

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

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
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


@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated was introduced on 3.9")
@pytest.mark.skipif(fastapi_version < (0, 95, 0), reason="Cookie annotation doesn't work on fastapi 94 or lower")
def test_cookies_source_typing_param(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(iast_cookie: typing.Annotated[str, Cookie()] = "ddd"):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

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

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
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


def test_path_param_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html/{item_id}")
    async def test_route(item_id):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

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

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
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


def test_path_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/path_source/")
    async def test_route(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        path = request.url.path
        ranges_result = get_tainted_ranges(path)

        return JSONResponse(
            {
                "result": path,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/path_source/",
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "/path_source/"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 13
        assert result["ranges_origin"] == "http.request.path"


def test_path_body_receive_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.post("/index.html")
    async def test_route(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        body = await request.receive()
        result = body["body"]
        ranges_result = get_tainted_ranges(result)

        return JSONResponse(
            {
                "result": str(result, encoding="utf-8"),
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post(
            "/index.html",
            data='{"name": "yqrweytqwreasldhkuqwgervflnmlnli"}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == '{"name": "yqrweytqwreasldhkuqwgervflnmlnli"}'
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 44
        assert result["ranges_origin"] == "http.request.body"


def test_path_body_body_source(fastapi_application, client, tracer, test_spans):
    @fastapi_application.post("/index.html")
    async def test_route(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        body = await request.body()
        ranges_result = get_tainted_ranges(body)

        return JSONResponse(
            {
                "result": str(body, encoding="utf-8"),
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post(
            "/index.html",
            data='{"name": "yqrweytqwreasldhkuqwgervflnmlnli"}',
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == '{"name": "yqrweytqwreasldhkuqwgervflnmlnli"}'
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 44
        assert result["ranges_origin"] == "http.request.body"


@pytest.mark.skipif(sys.version_info < (3, 9), reason="typing.Annotated was introduced on 3.9")
@pytest.mark.skipif(fastapi_version < (0, 95, 0), reason="Default is mandatory on 94 or lower")
def test_path_body_body_source_formdata_latest(fastapi_application, client, tracer, test_spans):
    @fastapi_application.post("/index.html")
    async def test_route(path: typing.Annotated[str, Form()]):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        ranges_result = get_tainted_ranges(path)

        return JSONResponse(
            {
                "result": path,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post("/index.html", data={"path": "/var/log"})
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "/var/log"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.body"


def test_path_body_body_source_formdata_90(fastapi_application, client, tracer, test_spans):
    @fastapi_application.post("/index.html")
    async def test_route(path: str = Form(...)):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        ranges_result = get_tainted_ranges(path)

        return JSONResponse(
            {
                "result": path,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post("/index.html", data={"path": "/var/log"})
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "/var/log"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.body"


@pytest.mark.skip(reason="Pydantic not supported yet APPSEC-52941")
def test_path_body_source_pydantic(fastapi_application, client, tracer, test_spans):
    from pydantic import BaseModel

    class Item(BaseModel):
        name: str
        description: str | None = None
        price: float | None = None
        tax: float | None = None

    @fastapi_application.post("/index")
    async def test_route(item: Item):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        ranges_result = get_tainted_ranges(item.name)

        return JSONResponse(
            {
                "result": item.name,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post(
            "/index", data='{"name": "yqrweytqwreasldhkuqwgervflnmlnli"}', headers={"Content-Type": "application/json"}
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["result"] == "test1234"
        assert result["is_tainted"] == 1
        assert result["ranges_start"] == 0
        assert result["ranges_length"] == 8
        assert result["ranges_origin"] == "http.request.body"


@pytest.mark.skipif(fastapi_version < (0, 65, 0), reason="UploadFile not supported")
def test_path_body_body_upload(fastapi_application, client, tracer, test_spans):
    @fastapi_application.post("/uploadfile/")
    async def create_upload_file(files: typing.List[UploadFile]):
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        ranges_result = get_tainted_ranges(files[0])
        return JSONResponse(
            {
                "filenames": [file.filename for file in files],
                "is_tainted": len(ranges_result),
            }
        )

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        tmp = io.BytesIO(b"upload this")
        resp = client.post(
            "/uploadfile/",
            files=(
                ("files", ("test.txt", tmp)),
                ("files", ("test2.txt", tmp)),
            ),
        )
        assert resp.status_code == 200
        result = json.loads(get_response_body(resp))
        assert result["filenames"] == ["test.txt", "test2.txt"]
        assert result["is_tainted"] == 0


def test_fastapi_sqli_path_param(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html/{param_str}")
    async def test_route(param_str):
        import sqlite3

        from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
        from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

        assert is_pyobject_tainted(param_str)

        con = sqlite3.connect(":memory:")
        cur = con.cursor()
        # label test_fastapi_sqli_path_parameter
        cur.execute(add_aspect("SELECT 1 FROM ", param_str))

    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        # disable callback
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html/sqlite_master/",
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[1][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        loaded = json.loads(span.get_tag(IAST.JSON))

        assert loaded["sources"] == [
            {"origin": "http.request.path.parameter", "name": "param_str", "value": "sqlite_master"}
        ]

        line, hash_value = get_line_and_hash(
            "test_fastapi_sqli_path_parameter", VULN_SQL_INJECTION, filename=TEST_FILE_PATH
        )
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_SQL_INJECTION
        assert vulnerability["evidence"] == {
            "valueParts": [
                {"value": "SELECT "},
                {"redacted": True},
                {"value": " FROM "},
                {"value": "sqlite_master", "source": 0},
            ]
        }
        assert vulnerability["location"]["line"] == line
        assert vulnerability["location"]["path"] == TEST_FILE_PATH
        assert vulnerability["location"]["method"] == "test_route"
        assert vulnerability["location"]["class_name"] == ""
        assert vulnerability["hash"] == hash_value


def test_fastapi_insecure_cookie(fastapi_application, client, tracer, test_spans):
    @fastapi_application.route("/insecure_cookie/", methods=["GET"])
    def insecure_cookie(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        query_params = request.query_params.get("iast_queryparam")
        ranges_result = get_tainted_ranges(query_params)
        response = JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

        # label test_fastapi_insecure_cookie
        response.set_cookie(key="insecure", value=query_params, secure=False, httponly=True, samesite="strict")

        return response

    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/insecure_cookie/?iast_queryparam=insecure",
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        loaded = json.loads(span.get_tag(IAST.JSON))
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_INSECURE_COOKIE
        assert vulnerability["location"]["spanId"]
        assert vulnerability["hash"]
        line, hash_value = get_line_and_hash(
            "test_fastapi_insecure_cookie", VULN_INSECURE_COOKIE, filename=TEST_FILE_PATH
        )
        assert vulnerability["location"]["line"] == line
        assert vulnerability["location"]["path"] == TEST_FILE_PATH


def test_fastapi_insecure_cookie_empty(fastapi_application, client, tracer, test_spans):
    @fastapi_application.route("/insecure_cookie/", methods=["GET"])
    def insecure_cookie(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        query_params = request.query_params.get("iast_queryparam")
        ranges_result = get_tainted_ranges(query_params)
        response = JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )
        response.set_cookie(key="insecure", value="", secure=False, httponly=True, samesite="strict")

        return response

    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/insecure_cookie/?iast_queryparam=insecure",
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        loaded = span.get_tag(IAST.JSON)
        assert loaded is None


def test_fastapi_no_http_only_cookie(fastapi_application, client, tracer, test_spans):
    @fastapi_application.route("/insecure_cookie/", methods=["GET"])
    def insecure_cookie(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        query_params = request.query_params.get("iast_queryparam")
        ranges_result = get_tainted_ranges(query_params)
        response = JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

        # label test_fastapi_no_http_only_cookie
        response.set_cookie(key="insecure", value=query_params, secure=True, httponly=False, samesite="strict")

        return response

    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/insecure_cookie/?iast_queryparam=insecure",
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        loaded = json.loads(span.get_tag(IAST.JSON))
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_NO_HTTPONLY_COOKIE
        assert vulnerability["location"]["spanId"]
        assert vulnerability["hash"]
        line, hash_value = get_line_and_hash(
            "test_fastapi_no_http_only_cookie", VULN_NO_HTTPONLY_COOKIE, filename=TEST_FILE_PATH
        )
        assert vulnerability["location"]["line"] == line
        assert vulnerability["location"]["path"] == TEST_FILE_PATH


def test_fastapi_no_http_only_cookie_empty(fastapi_application, client, tracer, test_spans):
    @fastapi_application.route("/insecure_cookie/", methods=["GET"])
    def insecure_cookie(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        query_params = request.query_params.get("iast_queryparam")
        ranges_result = get_tainted_ranges(query_params)
        response = JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )
        response.set_cookie(key="insecure", value="", secure=True, httponly=False, samesite="strict")

        return response

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/insecure_cookie/?iast_queryparam=insecure",
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        loaded = span.get_tag(IAST.JSON)
        assert loaded is None


def test_fastapi_no_samesite_cookie(fastapi_application, client, tracer, test_spans):
    @fastapi_application.route("/insecure_cookie/", methods=["GET"])
    def insecure_cookie(request: Request):
        from ddtrace.appsec._iast._taint_tracking import origin_to_str
        from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges

        query_params = request.query_params.get("iast_queryparam")
        ranges_result = get_tainted_ranges(query_params)
        response = JSONResponse(
            {
                "result": query_params,
                "is_tainted": len(ranges_result),
                "ranges_start": ranges_result[0].start,
                "ranges_length": ranges_result[0].length,
                "ranges_origin": origin_to_str(ranges_result[0].source.origin),
            }
        )

        # label test_fastapi_no_samesite_cookie
        response.set_cookie(key="insecure", value=query_params, secure=True, httponly=True, samesite="none")

        return response

    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/insecure_cookie/?iast_queryparam=insecure",
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        loaded = json.loads(span.get_tag(IAST.JSON))
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_NO_SAMESITE_COOKIE
        assert vulnerability["location"]["spanId"]
        assert vulnerability["hash"]
        line, hash_value = get_line_and_hash(
            "test_fastapi_no_samesite_cookie", VULN_NO_SAMESITE_COOKIE, filename=TEST_FILE_PATH
        )
        assert vulnerability["location"]["line"] == line
        assert vulnerability["location"]["path"] == TEST_FILE_PATH


def test_fastapi_header_injection(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/header_injection/")
    async def header_injection(request: Request):
        from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

        tainted_string = request.headers.get("test")
        assert is_pyobject_tainted(tainted_string)
        result_response = JSONResponse(content={"message": "OK"})
        # label test_fastapi_header_injection
        result_response.headers["Header-Injection"] = tainted_string
        result_response.headers["Vary"] = tainted_string
        result_response.headers["Foo"] = "bar"

        return result_response

    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _aux_appsec_prepare_tracer(tracer)
        patch_iast({"header_injection": True})
        resp = client.get(
            "/header_injection/",
            headers={"test": "test_injection_header"},
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        iast_tag = span.get_tag(IAST.JSON)
        assert iast_tag is not None
        loaded = json.loads(iast_tag)
        line, hash_value = get_line_and_hash(
            "test_fastapi_header_injection", VULN_HEADER_INJECTION, filename=TEST_FILE_PATH
        )
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_HEADER_INJECTION
        assert vulnerability["hash"] == hash_value
        assert vulnerability["location"]["line"] == line
        assert vulnerability["location"]["path"] == TEST_FILE_PATH
        assert vulnerability["location"]["method"] == "header_injection"
        assert vulnerability["location"]["class_name"] == ""
        assert vulnerability["location"]["spanId"]


def test_fastapi_header_injection_inline_response(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/header_injection_inline_response/", response_class=PlainTextResponse)
    async def header_injection_inline_response(request: Request):
        from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

        tainted_string = request.headers.get("test")
        assert is_pyobject_tainted(tainted_string)
        return PlainTextResponse(
            content="OK",
            headers={"Header-Injection": tainted_string, "Vary": tainted_string, "Foo": "bar"},
        )

    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _aux_appsec_prepare_tracer(tracer)
        patch_iast({"header_injection": True})
        resp = client.get(
            "/header_injection_inline_response/",
            headers={"test": "test_injection_header"},
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        iast_tag = span.get_tag(IAST.JSON)
        assert iast_tag is not None
        loaded = json.loads(iast_tag)
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_HEADER_INJECTION


def test_fastapi_stacktrace_leak(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/stacktrace_leak/", response_class=PlainTextResponse)
    async def stacktrace_leak_inline_response(request: Request):
        return PlainTextResponse(
            content=_load_text_stacktrace(),
        )

    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/stacktrace_leak/",
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        iast_tag = span.get_tag(IAST.JSON)
        assert iast_tag is not None
        loaded = json.loads(iast_tag)
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_STACKTRACE_LEAK


def test_fastapi_xss(fastapi_application, client, tracer, test_spans):
    @fastapi_application.get("/index.html")
    async def test_route(request: Request):
        from fastapi.responses import HTMLResponse
        from jinja2 import Template

        query_params = request.query_params.get("iast_queryparam")
        template = Template("<p>{{ user_input|safe }}</p>")
        html = template.render(user_input=query_params)
        return HTMLResponse(html)

    with override_global_config(dict(_iast_enabled=True, _iast_request_sampling=100.0)):
        patch_iast({"xss": True})
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get(
            "/index.html?iast_queryparam=test1234",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 200

        span = test_spans.pop_traces()[0][0]
        assert span.get_metric(IAST.ENABLED) == 1.0

        iast_tag = span.get_tag(IAST.JSON)
        assert iast_tag is not None
        loaded = json.loads(iast_tag)
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_XSS
