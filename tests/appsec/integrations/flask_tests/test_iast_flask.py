import json
import sys

from flask import request
from importlib_metadata import version
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._iast_request_context import _iast_start_request
from ddtrace.appsec._iast._patches.json_tainting import patch as patch_json
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_HTTPONLY_COOKIE
from ddtrace.appsec._iast.constants import VULN_NO_SAMESITE_COOKIE
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.taint_sinks.header_injection import patch as patch_header_injection
from ddtrace.contrib.internal.sqlite3.patch import patch as patch_sqlite_sqli
from ddtrace.settings.asm import config as asm_config
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.contrib.flask import BaseFlaskTestCase
from tests.utils import override_global_config


TEST_FILE_PATH = "tests/appsec/integrations/flask_tests/test_iast_flask.py"

werkzeug_version = version("werkzeug")
flask_version = tuple([int(v) for v in version("flask").split(".")])


class FlaskAppSecIASTEnabledTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog, telemetry_writer):  # noqa: F811
        self._telemetry_writer = telemetry_writer
        self._caplog = caplog

    def setUp(self):
        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            super(FlaskAppSecIASTEnabledTestCase, self).setUp()
            patch_sqlite_sqli()
            patch_header_injection()
            patch_json()

            self.tracer._configure(api_version="v0.4", iast_enabled=True)
            oce.reconfigure()

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_http_request_path_parameter(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def sqli_1(param_str):
            import sqlite3

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            assert is_pyobject_tainted(param_str)
            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            # label test_flask_full_sqli_iast_http_request_path_parameter
            cur.execute(add_aspect("SELECT 1 FROM ", param_str))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            resp = self.client.post("/sqli/sqlite_master/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [
                {"origin": "http.request.path.parameter", "name": "param_str", "value": "sqlite_master"}
            ]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_http_request_path_parameter", VULN_SQL_INJECTION, filename=TEST_FILE_PATH
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
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_header_getitem(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def sqli_2(param_str):
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            # label test_flask_full_sqli_iast_enabled_http_request_header_getitem
            cur.execute(add_aspect("SELECT 1 FROM ", request.headers["User-Agent"]))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post(
                "/sqli/sqlite_master/", data={"name": "test"}, headers={"User-Agent": "sqlite_master"}
            )
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [
                {"origin": "http.request.header", "name": "User-Agent", "value": "sqlite_master"}
            ]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_enabled_http_request_header_getitem",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
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
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_header_name_keys(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def sqli_3(param_str):
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            # Test to consume request.header.keys twice
            _ = [k for k in request.headers.keys() if k == "Master"][0]
            header_name = [k for k in request.headers.keys() if k == "Master"][0]
            # label test_flask_full_sqli_iast_enabled_http_request_header_name_keys
            cur.execute(add_aspect("SELECT 1 FROM sqlite_", header_name))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ):
            resp = self.client.post("/sqli/sqlite_master/", data={"name": "test"}, headers={"master": "not_user_agent"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"origin": "http.request.header.name", "name": "Master", "value": "Master"}]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_enabled_http_request_header_name_keys",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [
                    {"value": "SELECT "},
                    {"redacted": True},
                    {"value": " FROM sqlite_"},
                    {"value": "Master", "source": 0},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_header_values(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def sqli_4(param_str):
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            header = [k for k in request.headers.values() if k == "master"][0]
            # label test_flask_full_sqli_iast_enabled_http_request_header_values
            cur.execute(add_aspect("SELECT 1 FROM sqlite_", header))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ):
            resp = self.client.post("/sqli/sqlite_master/", data={"name": "test"}, headers={"user-agent": "master"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"origin": "http.request.header", "name": "User-Agent", "value": "master"}]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_enabled_http_request_header_values",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [
                    {"value": "SELECT "},
                    {"redacted": True},
                    {"value": " FROM sqlite_"},
                    {"value": "master", "source": 0},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(sys.version_info < (3, 8), reason="some requests params fail in Python 3.7 or lower")
    def test_flask_simple_iast_path_header_and_querystring_tainted(self):
        @self.app.route("/sqli/<string:param_str>/<int:param_int>/", methods=["GET", "POST"])
        def sqli_5(param_str, param_int):
            from flask import request

            from ddtrace.appsec._iast._taint_tracking import OriginType
            from ddtrace.appsec._iast._taint_tracking._taint_objects import get_tainted_ranges
            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            header_ranges = get_tainted_ranges(request.headers["User-Agent"])
            assert header_ranges
            assert header_ranges[0].source.name.lower() == "user-agent"
            assert header_ranges[0].source.origin == OriginType.HEADER

            if flask_version > (2, 0):
                query_string_ranges = get_tainted_ranges(request.query_string)
                assert query_string_ranges
                assert query_string_ranges[0].source.name == "http.request.query"
                assert query_string_ranges[0].source.origin == OriginType.QUERY

                request_path_ranges = get_tainted_ranges(request.path)
                assert request_path_ranges
                assert request_path_ranges[0].source.name == "http.request.path"
                assert request_path_ranges[0].source.origin == OriginType.PATH

            _ = get_tainted_ranges(param_str)
            assert not is_pyobject_tainted(param_int)

            request_form_name_ranges = get_tainted_ranges(request.form.get("name"))
            assert request_form_name_ranges
            assert request_form_name_ranges[0].source.name == "name"
            assert request_form_name_ranges[0].source.origin == OriginType.PARAMETER

            return request.query_string, 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            resp = self.client.post("/sqli/hello/1000/?select%20from%20table", data={"name": "test"})
            assert resp.status_code == 200
            if hasattr(resp, "text"):
                # not all flask versions have r.text
                assert resp.text == "select%20from%20table"

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_simple_iast_path_header_and_querystring_tainted_request_sampling_0(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def sqli_6(param_str):
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            # Note: these are not tainted because of request sampling at 0%
            assert not is_pyobject_tainted(request.headers["User-Agent"])
            assert not is_pyobject_tainted(request.query_string)
            assert not is_pyobject_tainted(param_str)
            assert not is_pyobject_tainted(request.path)
            assert not is_pyobject_tainted(request.form.get("name"))

            return request.query_string, 200

        class MockSpan:
            _trace_id_64bits = 17577308072598193742

        with override_global_config(
            dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=0.0)
        ):
            oce.reconfigure()
            _iast_start_request(MockSpan())
            resp = self.client.post("/sqli/hello/?select%20from%20table", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]

            assert root_span.get_metric(IAST.ENABLED) == 0.0

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_cookies_value(self):
        @self.app.route("/sqli/cookies/", methods=["GET", "POST"])
        def sqli_7():
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            # label test_flask_full_sqli_iast_enabled_http_request_cookies_value
            cur.execute(add_aspect("SELECT 1 FROM ", request.cookies.get("test-cookie1")))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            oce.reconfigure()

            if tuple(map(int, werkzeug_version.split("."))) >= (2, 3):
                self.client.set_cookie(domain="localhost", key="test-cookie1", value="sqlite_master")
            else:
                self.client.set_cookie(server_name="localhost", key="test-cookie1", value="sqlite_master")

            resp = self.client.post("/sqli/cookies/")
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [
                {"origin": "http.request.cookie.value", "name": "test-cookie1", "value": "sqlite_master"}
            ]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_enabled_http_request_cookies_value",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = False
            for vuln in loaded["vulnerabilities"]:
                if vuln["type"] == VULN_SQL_INJECTION:
                    vulnerability = vuln

            assert vulnerability, "No {} reported".format(VULN_SQL_INJECTION)
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
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_cookies_name(self):
        @self.app.route("/sqli/cookies/", methods=["GET", "POST"])
        def sqli_8():
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            key = [x for x in request.cookies.keys() if x == "sqlite_master"][0]
            # label test_flask_full_sqli_iast_enabled_http_request_cookies_name
            cur.execute(add_aspect("SELECT 1 FROM ", key))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            if tuple(map(int, werkzeug_version.split("."))) >= (2, 3):
                self.client.set_cookie(domain="localhost", key="sqlite_master", value="sqlite_master2")
            else:
                self.client.set_cookie(server_name="localhost", key="sqlite_master", value="sqlite_master2")

            resp = self.client.post("/sqli/cookies/")
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [
                {"origin": "http.request.cookie.name", "name": "sqlite_master", "value": "sqlite_master"}
            ]
            vulnerabilities = set()
            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_enabled_http_request_cookies_name",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )

            for vulnerability in loaded["vulnerabilities"]:
                vulnerabilities.add(vulnerability["type"])
                if vulnerability["type"] == VULN_SQL_INJECTION:
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
                    assert vulnerability["hash"] == hash_value

            assert {VULN_SQL_INJECTION} == vulnerabilities

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_http_request_parameter(self):
        @self.app.route("/sqli/parameter/", methods=["GET"])
        def sqli_9():
            import sqlite3

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            # label test_flask_full_sqli_iast_http_request_parameter
            cur.execute(add_aspect("SELECT 1 FROM ", request.args.get("table")))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.get("/sqli/parameter/?table=sqlite_master")
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [
                {"origin": "http.request.parameter", "name": "table", "value": "sqlite_master"}
            ]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_http_request_parameter", VULN_SQL_INJECTION, filename=TEST_FILE_PATH
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
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_http_request_parameter_name_post(self):
        @self.app.route("/sqli/", methods=["POST"])
        def sqli_13():
            import sqlite3

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            for i in request.form.keys():
                assert is_pyobject_tainted(i)

            first_param = list(request.form.keys())[0]

            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            # label test_flask_full_sqli_iast_http_request_parameter_name_post
            cur.execute(add_aspect("SELECT 1 FROM ", first_param))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            resp = self.client.post("/sqli/", data={"sqlite_master": "unused"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [
                {"origin": "http.request.parameter.name", "name": "sqlite_master", "value": "sqlite_master"}
            ]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_http_request_parameter_name_post",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
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
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_http_request_parameter_name_get(self):
        @self.app.route("/sqli/", methods=["GET"])
        def sqli_14():
            import sqlite3

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            for i in request.args.keys():
                assert is_pyobject_tainted(i)

            first_param = list(request.args.keys())[0]

            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            # label test_flask_full_sqli_iast_http_request_parameter_name_get
            cur.execute(add_aspect("SELECT 1 FROM ", first_param))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            resp = self.client.get("/sqli/", query_string={"sqlite_master": "unused"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [
                {"origin": "http.request.parameter.name", "name": "sqlite_master", "value": "sqlite_master"}
            ]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_http_request_parameter_name_get",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
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
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_request_body(self):
        @self.app.route("/sqli/body/", methods=("POST",))
        def sqli_10():
            import json
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            if flask_version > (2, 0):
                json_data = request.json
            else:
                json_data = json.loads(request.data)
            value = json_data.get("json_body")
            assert value == "master"

            assert is_pyobject_tainted(value)
            query = add_aspect(add_aspect("SELECT tbl_name FROM sqlite_", value), " WHERE tbl_name LIKE 'password'")
            # label test_flask_request_body
            cur.execute(query)

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            resp = self.client.post(
                "/sqli/body/", data=json.dumps(dict(json_body="master")), content_type="application/json"
            )
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"name": "json_body", "origin": "http.request.body", "value": "master"}]

            line, hash_value = get_line_and_hash(
                "test_flask_request_body",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [
                    {"value": "SELECT tbl_name FROM sqlite_"},
                    {"value": "master", "source": 0},
                    {"value": " WHERE tbl_name LIKE '"},
                    {"redacted": True},
                    {"value": "'"},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_request_body_complex_3_lvls(self):
        @self.app.route("/sqli/body/", methods=("POST",))
        def sqli_11():
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            if flask_version > (2, 0):
                json_data = request.json
            else:
                json_data = json.loads(request.data)
            value = json_data.get("body").get("body2").get("body3")
            assert value == "master"
            assert is_pyobject_tainted(value)
            query = add_aspect(add_aspect("SELECT tbl_name FROM sqlite_", value), " WHERE tbl_name LIKE 'password'")
            # label test_flask_request_body_complex_3_lvls
            cur.execute(query)

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ):
            resp = self.client.post(
                "/sqli/body/",
                data=json.dumps(dict(body=dict(body2=dict(body3="master")))),
                content_type="application/json",
            )
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"name": "body3", "origin": "http.request.body", "value": "master"}]

            line, hash_value = get_line_and_hash(
                "test_flask_request_body_complex_3_lvls",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [
                    {"value": "SELECT tbl_name FROM sqlite_"},
                    {"value": "master", "source": 0},
                    {"value": " WHERE tbl_name LIKE '"},
                    {"redacted": True},
                    {"value": "'"},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_request_body_complex_3_lvls_and_list(self):
        @self.app.route("/sqli/body/", methods=("POST",))
        def sqli_11():
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            if flask_version > (2, 0):
                json_data = request.json
            else:
                json_data = json.loads(request.data)
            value = json_data.get("body").get("body2").get("body3")[3]
            assert value == "master"
            assert is_pyobject_tainted(value)
            query = add_aspect(add_aspect("SELECT tbl_name FROM sqlite_", value), " WHERE tbl_name LIKE 'password'")
            # label test_flask_request_body_complex_3_lvls_and_list
            cur.execute(query)

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ):
            resp = self.client.post(
                "/sqli/body/",
                data=json.dumps(dict(body=dict(body2=dict(body3=["master3", "master2", "master1", "master"])))),
                content_type="application/json",
            )
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"name": "body3", "origin": "http.request.body", "value": "master"}]

            line, hash_value = get_line_and_hash(
                "test_flask_request_body_complex_3_lvls_and_list",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [
                    {"value": "SELECT tbl_name FROM sqlite_"},
                    {"value": "master", "source": 0},
                    {"value": " WHERE tbl_name LIKE '"},
                    {"redacted": True},
                    {"value": "'"},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_request_body_complex_3_lvls_list_dict(self):
        @self.app.route("/sqli/body/", methods=("POST",))
        def sqli_11():
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            if flask_version > (2, 0):
                json_data = request.json
            else:
                json_data = json.loads(request.data)
            value = json_data.get("body").get("body2").get("body3")[3].get("body4")
            assert value == "master"
            assert is_pyobject_tainted(value)
            query = add_aspect(add_aspect("SELECT tbl_name FROM sqlite_", value), " WHERE tbl_name LIKE 'password'")
            # label test_flask_request_body_complex_3_lvls_list_dict
            cur.execute(query)

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ):
            resp = self.client.post(
                "/sqli/body/",
                data=json.dumps(
                    dict(body=dict(body2=dict(body3=["master3", "master2", "master1", {"body4": "master"}])))
                ),
                content_type="application/json",
            )
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"name": "body4", "origin": "http.request.body", "value": "master"}]

            line, hash_value = get_line_and_hash(
                "test_flask_request_body_complex_3_lvls_list_dict",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [
                    {"value": "SELECT tbl_name FROM sqlite_"},
                    {"value": "master", "source": 0},
                    {"value": " WHERE tbl_name LIKE '"},
                    {"redacted": True},
                    {"value": "'"},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_request_body_complex_json_all_types_of_values(self):
        @self.app.route("/sqli/body/", methods=("POST",))
        def sqli_11():
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            def iterate_json(data, parent_key=""):
                if isinstance(data, dict):
                    for key, value in data.items():
                        iterate_json(value, key)
                elif isinstance(data, list):
                    for index, item in enumerate(data):
                        iterate_json(item, parent_key)
                else:
                    assert is_pyobject_tainted(parent_key), f"{parent_key} taint error"
                    if isinstance(data, str):
                        assert is_pyobject_tainted(data), f"{parent_key}.{data} taint error"
                    else:
                        assert not is_pyobject_tainted(data), f"{parent_key}.{data} taint error"

            if flask_version > (2, 0):
                request_json = request.json
            else:
                request_json = json.loads(request.data)

            iterate_json(request_json)

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            value = request_json.get("user").get("profile").get("preferences").get("extra")
            assert value == "master"
            assert is_pyobject_tainted(value)
            query = add_aspect(add_aspect("SELECT tbl_name FROM sqlite_", value), " WHERE tbl_name LIKE 'password'")
            # label test_flask_request_body_complex_json_all_types_of_values
            cur.execute(query)

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ):
            # random json with all kind of types
            json_data = {
                "user": {
                    "id": 12345,
                    "name": "John Doe",
                    "email": "johndoe@example.com",
                    "profile": {
                        "age": 30,
                        "gender": "male",
                        "preferences": {
                            "language": "English",
                            "timezone": "GMT+0",
                            "notifications": True,
                            "theme": "dark",
                            "extra": "master",
                        },
                        "social_links": ["https://twitter.com/johndoe", "https://github.com/johndoe"],
                    },
                },
                "settings": {
                    "volume": 80,
                    "brightness": 50,
                    "wifi": {
                        "enabled": True,
                        "networks": [
                            {"ssid": "HomeNetwork", "signal_strength": -40, "secured": True},
                            {"ssid": "WorkNetwork", "signal_strength": -60, "secured": False},
                        ],
                    },
                },
                "tasks": [
                    {"task_id": 1, "title": "Finish project report", "due_date": "2024-08-25", "completed": False},
                    {
                        "task_id": 2,
                        "title": "Buy groceries",
                        "due_date": "2024-08-23",
                        "completed": True,
                        "items": ["milk", "bread", "eggs"],
                    },
                ],
                "random_values": [
                    42,
                    "randomString",
                    True,
                    None,
                    [3.14, 2.71, 1.618],
                    {"nested_key": "nestedValue", "nested_number": 999, "nested_array": [1, "two", None]},
                ],
                "system": {
                    "os": "Linux",
                    "version": "5.10",
                    "uptime": 1234567,
                    "processes": {"running": 345, "sleeping": 56, "stopped": 2},
                },
            }

            resp = self.client.post(
                "/sqli/body/",
                data=json.dumps(json_data),
                content_type="application/json",
            )
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"name": "extra", "origin": "http.request.body", "value": "master"}]

            line, hash_value = get_line_and_hash(
                "test_flask_request_body_complex_json_all_types_of_values",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [
                    {"value": "SELECT tbl_name FROM sqlite_"},
                    {"value": "master", "source": 0},
                    {"value": " WHERE tbl_name LIKE '"},
                    {"redacted": True},
                    {"value": "'"},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_request_body_iast_and_appsec(self):
        """Verify IAST, Appsec and API security work correctly running at the same time"""

        @self.app.route("/sqli/body/", methods=("POST",))
        def sqli_10():
            import json
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()
            if flask_version > (2, 0):
                json_data = request.json
            else:
                json_data = json.loads(request.data)
            value = json_data.get("json_body")
            assert value == "master"

            assert is_pyobject_tainted(value)
            query = add_aspect(add_aspect("SELECT tbl_name FROM sqlite_", value), " WHERE tbl_name LIKE 'password'")
            # label test_flask_request_body
            cur.execute(query)

            return {"Response": value}, 200

        with override_global_config(
            dict(
                _iast_enabled=True,
                _asm_enabled=True,
                _api_security_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            resp = self.client.post(
                "/sqli/body/", data=json.dumps(dict(json_body="master")), content_type="application/json"
            )
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"name": "json_body", "origin": "http.request.body", "value": "master"}]

            list_metrics_logs = list(self._telemetry_writer._logs)
            assert len(list_metrics_logs) == 0

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_header_values_scrubbed(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def sqli_12(param_str):
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            header = [k for k in request.headers.values() if k == "master"][0]
            query = add_aspect(add_aspect("SELECT tbl_name FROM sqlite_", header), " WHERE tbl_name LIKE 'password'")
            # label test_flask_full_sqli_iast_enabled_http_request_header_values_scrubbed
            cur.execute(query)

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ):
            resp = self.client.post("/sqli/sqlite_master/", data={"name": "test"}, headers={"user-agent": "master"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"origin": "http.request.header", "name": "User-Agent", "value": "master"}]

            line, hash_value = get_line_and_hash(
                "test_flask_full_sqli_iast_enabled_http_request_header_values_scrubbed",
                VULN_SQL_INJECTION,
                filename=TEST_FILE_PATH,
            )
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [
                    {"value": "SELECT tbl_name FROM sqlite_"},
                    {"value": "master", "source": 0},
                    {"value": " WHERE tbl_name LIKE '"},
                    {"redacted": True},
                    {"value": "'"},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_header_injection(self):
        @self.app.route("/header_injection/", methods=["GET", "POST"])
        def header_injection():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.headers["Vary"] = tainted_string

            # label test_flask_header_injection_label
            resp.headers["Header-Injection"] = tainted_string
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post("/header_injection/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == [{"origin": "http.request.parameter", "name": "name", "value": "test"}]

            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_HEADER_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [{"value": "Header-Injection: "}, {"source": 0, "value": "test"}]
            }
            # TODO: vulnerability path is flaky, it points to "tests/contrib/flask/__init__.py"

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_header_injection_exlusions_location(self):
        @self.app.route("/header_injection/", methods=["GET", "POST"])
        def header_injection():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.headers["Location"] = tainted_string
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post("/header_injection/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            assert root_span.get_tag(IAST.JSON) is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_header_injection_exlusions_access_control(self):
        @self.app.route("/header_injection/", methods=["GET", "POST"])
        def header_injection():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.headers["Access-Control-Allow-Example1"] = tainted_string
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post("/header_injection/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            assert root_span.get_tag(IAST.JSON) is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_insecure_cookie(self):
        @self.app.route("/insecure_cookie/", methods=["GET", "POST"])
        def insecure_cookie():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.set_cookie("insecure", "cookie", secure=False, httponly=True, samesite="Strict")
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post("/insecure_cookie/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == []
            assert len(loaded["vulnerabilities"]) == 1
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_INSECURE_COOKIE
            assert vulnerability["evidence"] == {"valueParts": [{"value": "insecure"}]}
            assert "path" not in vulnerability["location"].keys()
            assert "line" not in vulnerability["location"].keys()
            assert vulnerability["location"]["spanId"]
            assert vulnerability["hash"]

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_insecure_cookie_empty(self):
        @self.app.route("/insecure_cookie_empty/", methods=["GET", "POST"])
        def insecure_cookie_empty():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.set_cookie("insecure", "", secure=False, httponly=True, samesite="Strict")
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post("/insecure_cookie_empty/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = root_span.get_tag(IAST.JSON)
            assert loaded is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_no_http_only_cookie(self):
        @self.app.route("/no_http_only_cookie/", methods=["GET", "POST"])
        def no_http_only_cookie():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.set_cookie("insecure", "cookie", secure=True, httponly=False, samesite="Strict")
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post("/no_http_only_cookie/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == []
            assert len(loaded["vulnerabilities"]) == 1
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_NO_HTTPONLY_COOKIE
            assert vulnerability["evidence"] == {"valueParts": [{"value": "insecure"}]}
            assert "path" not in vulnerability["location"].keys()
            assert "line" not in vulnerability["location"].keys()
            assert vulnerability["location"]["spanId"]
            assert vulnerability["hash"]

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_no_http_only_cookie_empty(self):
        @self.app.route("/no_http_only_cookie_empty/", methods=["GET", "POST"])
        def no_http_only_cookie_empty():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.set_cookie("insecure", "", secure=True, httponly=False, samesite="Strict")
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            resp = self.client.post("/no_http_only_cookie_empty/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = root_span.get_tag(IAST.JSON)
            assert loaded is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_no_samesite_cookie(self):
        @self.app.route("/no_samesite_cookie/", methods=["GET", "POST"])
        def no_samesite_cookie():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.set_cookie("insecure", "cookie", secure=True, httponly=True, samesite="None")
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post("/no_samesite_cookie/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert loaded["sources"] == []
            assert len(loaded["vulnerabilities"]) == 1
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_NO_SAMESITE_COOKIE
            assert vulnerability["evidence"] == {"valueParts": [{"value": "insecure"}]}
            assert "path" not in vulnerability["location"].keys()
            assert "line" not in vulnerability["location"].keys()
            assert vulnerability["location"]["spanId"]
            assert vulnerability["hash"]

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_no_samesite_cookie_empty(self):
        @self.app.route("/no_samesite_cookie_empty/", methods=["GET", "POST"])
        def no_samesite_cookie_empty():
            from flask import Response
            from flask import request

            from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.set_cookie("insecure", "", secure=True, httponly=True, samesite="None")
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
            )
        ):
            resp = self.client.post("/no_samesite_cookie_empty/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            loaded = root_span.get_tag(IAST.JSON)
            assert loaded is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_cookie_secure(self):
        @self.app.route("/cookie_secure/", methods=["GET", "POST"])
        def cookie_secure():
            from flask import Response
            from flask import request

            tainted_string = request.form.get("name")
            assert is_pyobject_tainted(tainted_string)
            resp = Response("OK")
            resp.set_cookie("insecure", "cookie", secure=True, httponly=True, samesite="Strict")
            return resp

        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            resp = self.client.post("/cookie_secure/", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

            loaded = root_span.get_tag(IAST.JSON)
            assert loaded is None


class FlaskAppSecIASTDisabledTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def setUp(self):
        with override_global_config(
            dict(
                _iast_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            super(FlaskAppSecIASTDisabledTestCase, self).setUp()
            self.tracer._configure(api_version="v0.4")

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_disabled_http_request_cookies_name(self):
        @self.app.route("/sqli/cookies/", methods=["GET", "POST"])
        def test_sqli():
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            key = [x for x in request.cookies.keys() if x == "sqlite_master"][0]
            cur.execute(add_aspect("SELECT 1 FROM ", key))

            return "OK", 200

        with override_global_config(dict(_iast_enabled=False)):
            if tuple(map(int, werkzeug_version.split("."))) >= (2, 3):
                self.client.set_cookie(domain="localhost", key="sqlite_master", value="sqlite_master3")
            else:
                self.client.set_cookie(server_name="localhost", key="sqlite_master", value="sqlite_master3")

            resp = self.client.post("/sqli/cookies/")
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) is None

            assert root_span.get_tag(IAST.JSON) is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_disabled_http_request_header_getitem(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            cur.execute(add_aspect("SELECT 1 FROM ", request.headers["User-Agent"]))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=False,
            )
        ):
            resp = self.client.post(
                "/sqli/sqlite_master/", data={"name": "test"}, headers={"User-Agent": "sqlite_master"}
            )
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) is None

            assert root_span.get_tag(IAST.JSON) is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_disabled_http_request_header_name_keys(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            header_name = [k for k in request.headers.keys() if k == "Master"][0]

            cur.execute(add_aspect("SELECT 1 FROM sqlite_", header_name))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=False,
            )
        ):
            resp = self.client.post("/sqli/sqlite_master/", data={"name": "test"}, headers={"master": "not_user_agent"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) is None

            assert root_span.get_tag(IAST.JSON) is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_disabled_http_request_header_values(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            header = [k for k in request.headers.values() if k == "master"][0]

            cur.execute(add_aspect("SELECT 1 FROM sqlite_", header))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=False,
            )
        ):
            resp = self.client.post("/sqli/sqlite_master/", data={"name": "test"}, headers={"user-agent": "master"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) is None

            assert root_span.get_tag(IAST.JSON) is None

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_simple_iast_path_header_and_querystring_not_tainted_if_iast_disabled(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
            from flask import request

            assert not is_pyobject_tainted(request.headers["User-Agent"])
            assert not is_pyobject_tainted(request.query_string)
            assert not is_pyobject_tainted(param_str)
            assert not is_pyobject_tainted(request.path)
            assert not is_pyobject_tainted(request.form.get("name"))
            return request.query_string, 200

        with override_global_config(
            dict(
                _iast_enabled=False,
            )
        ):
            resp = self.client.post("/sqli/hello/?select%20from%20table", data={"name": "test"})
            assert resp.status_code == 200
            if hasattr(resp, "text"):
                # not all flask versions have r.text
                assert resp.text == "select%20from%20table"

    @pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_disabled_http_request_cookies_value(self):
        @self.app.route("/sqli/cookies/", methods=["GET", "POST"])
        def test_sqli():
            import sqlite3

            from flask import request

            from ddtrace.appsec._iast._taint_tracking.aspects import add_aspect

            con = sqlite3.connect(":memory:")
            cur = con.cursor()

            cur.execute(add_aspect("SELECT 1 FROM ", request.cookies.get("test-cookie1")))

            return "OK", 200

        with override_global_config(
            dict(
                _iast_enabled=False,
            )
        ):
            if tuple(map(int, werkzeug_version.split("."))) >= (2, 3):
                self.client.set_cookie(domain="localhost", key="test-cookie1", value="sqlite_master")
            else:
                self.client.set_cookie(server_name="localhost", key="test-cookie1", value="sqlite_master")

            resp = self.client.post("/sqli/cookies/")
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) is None

            assert root_span.get_tag(IAST.JSON) is None
