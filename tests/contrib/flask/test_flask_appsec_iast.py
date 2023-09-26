import json

from flask import request
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.contrib.sqlite3.patch import patch
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.contrib.flask import BaseFlaskTestCase
from tests.utils import override_env
from tests.utils import override_global_config


TEST_FILE_PATH = "tests/contrib/flask/test_flask_appsec_iast.py"
IAST_ENV = {"DD_IAST_REQUEST_SAMPLING": "100"}
IAST_ENV_SAMPLING_0 = {"DD_IAST_REQUEST_SAMPLING": "0"}


@pytest.fixture(autouse=True)
def reset_context():
    from ddtrace.appsec._iast._taint_tracking import create_context
    from ddtrace.appsec._iast._taint_tracking import reset_context

    yield
    reset_context()
    _ = create_context()


class FlaskAppSecIASTEnabledTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def setUp(self):
        with override_global_config(
            dict(
                _iast_enabled=True,
                _appsec_enabled=True,
            )
        ), override_env(IAST_ENV):
            super(FlaskAppSecIASTEnabledTestCase, self).setUp()
            patch()
            oce.reconfigure()

            self.tracer._iast_enabled = True
            self.tracer._appsec_enabled = True
            self.tracer.configure(api_version="v0.4")

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_http_request_path_parameter(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
            import sqlite3

            from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
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
                _appsec_enabled=True,
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
                "valueParts": [{"value": "SELECT 1 FROM "}, {"value": "sqlite_master", "source": 0}]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_header_getitem(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
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
                _appsec_enabled=True,
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
                "valueParts": [{"value": "SELECT 1 FROM "}, {"value": "sqlite_master", "source": 0}]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_header_name_keys(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
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
                "valueParts": [{"value": "SELECT 1 FROM sqlite_"}, {"value": "Master", "source": 0}]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_header_values(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
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
                "valueParts": [{"value": "SELECT 1 FROM sqlite_"}, {"value": "master", "source": 0}]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_simple_iast_path_header_and_querystring_tainted(self):
        @self.app.route("/sqli/<string:param_str>/<int:param_int>/", methods=["GET", "POST"])
        def test_sqli(param_str, param_int):
            from flask import request

            from ddtrace.appsec._iast._taint_tracking import OriginType
            from ddtrace.appsec._iast._taint_tracking import get_tainted_ranges
            from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

            header_ranges = get_tainted_ranges(request.headers["User-Agent"])
            assert header_ranges
            assert header_ranges[0].source.name.lower() == "user-agent"
            assert header_ranges[0].source.origin == OriginType.HEADER

            _ = get_tainted_ranges(request.query_string)
            # TODO: this test fails in 3.7
            # assert query_string_ranges
            # assert query_string_ranges[0].source.name == "http.request.query"
            # assert query_string_ranges[0].source.origin == OriginType.QUERY

            _ = get_tainted_ranges(param_str)
            # TODO: this test fails in 3.7
            # assert param_str_ranges
            # assert param_str_ranges[0].source.name == "param_str"
            # assert param_str_ranges[0].source.origin == OriginType.PATH_PARAMETER

            assert not is_pyobject_tainted(param_int)

            _ = get_tainted_ranges(request.path)
            # TODO: this test fails in 3.7
            # assert request_path_ranges
            # assert request_path_ranges[0].source.name == "http.request.path"
            # assert request_path_ranges[0].source.origin == OriginType.PATH

            request_form_name_ranges = get_tainted_ranges(request.form.get("name"))
            assert request_form_name_ranges
            assert request_form_name_ranges[0].source.name == "name"
            assert request_form_name_ranges[0].source.origin == OriginType.PARAMETER

            return request.query_string, 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ):
            resp = self.client.post("/sqli/hello/1000/?select%20from%20table", data={"name": "test"})
            assert resp.status_code == 200
            if hasattr(resp, "text"):
                # not all flask versions have r.text
                assert resp.text == "select%20from%20table"

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 1.0

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_simple_iast_path_header_and_querystring_tainted_request_sampling_0(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
            from flask import request

            from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

            # Note: these are not tainted because of request sampling at 0%
            assert not is_pyobject_tainted(request.headers["User-Agent"])
            assert not is_pyobject_tainted(request.query_string)
            assert not is_pyobject_tainted(param_str)
            assert not is_pyobject_tainted(request.path)
            assert not is_pyobject_tainted(request.form.get("name"))

            return request.query_string, 200

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ), override_env(IAST_ENV_SAMPLING_0):
            oce.reconfigure()

            resp = self.client.post("/sqli/hello/?select%20from%20table", data={"name": "test"})
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) == 0.0

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_cookies_value(self):
        @self.app.route("/sqli/cookies/", methods=["GET", "POST"])
        def test_sqli():
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
            )
        ), override_env(IAST_ENV):
            oce.reconfigure()
            self.client.set_cookie("localhost", "test-cookie1", "sqlite_master")
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
            vulnerability = loaded["vulnerabilities"][0]
            assert vulnerability["type"] == VULN_SQL_INJECTION
            assert vulnerability["evidence"] == {
                "valueParts": [{"value": "SELECT 1 FROM "}, {"value": "sqlite_master", "source": 0}]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_cookies_name(self):
        @self.app.route("/sqli/cookies/", methods=["GET", "POST"])
        def test_sqli():
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
                _appsec_enabled=True,
            )
        ):
            self.client.set_cookie("localhost", "sqlite_master", "sqlite_master2")
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
                        "valueParts": [{"value": "SELECT 1 FROM "}, {"value": "sqlite_master", "source": 0}]
                    }
                    assert vulnerability["location"]["line"] == line
                    assert vulnerability["location"]["path"] == TEST_FILE_PATH
                    assert vulnerability["hash"] == hash_value

            assert {VULN_SQL_INJECTION, "INSECURE_COOKIE"} == vulnerabilities

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_http_request_parameter(self):
        @self.app.route("/sqli/parameter/", methods=["GET"])
        def test_sqli():
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
                "valueParts": [{"value": "SELECT 1 FROM "}, {"value": "sqlite_master", "source": 0}]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_full_sqli_iast_enabled_http_request_header_values_scrubbed(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
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
                    {"pattern": " WHERE tbl_name LIKE '********'", "redacted": True},
                ]
            }
            assert vulnerability["location"]["line"] == line
            assert vulnerability["location"]["path"] == TEST_FILE_PATH
            assert vulnerability["hash"] == hash_value


class FlaskAppSecIASTDisabledTestCase(BaseFlaskTestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self, caplog):
        self._caplog = caplog

    def setUp(self):
        with override_global_config(
            dict(
                _iast_enabled=False,
            )
        ), override_env({"DD_IAST_REQUEST_SAMPLING": "100"}):
            super(FlaskAppSecIASTDisabledTestCase, self).setUp()
            self.tracer._iast_enabled = False
            self.tracer._appsec_enabled = False
            self.tracer.configure(api_version="v0.4")

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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

        self.client.set_cookie("localhost", "sqlite_master", "sqlite_master3")
        resp = self.client.post("/sqli/cookies/")
        assert resp.status_code == 200

        root_span = self.pop_spans()[0]
        assert root_span.get_metric(IAST.ENABLED) is None

        assert root_span.get_tag(IAST.JSON) is None

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
    def test_flask_simple_iast_path_header_and_querystring_not_tainted_if_iast_disabled(self):
        @self.app.route("/sqli/<string:param_str>/", methods=["GET", "POST"])
        def test_sqli(param_str):
            from flask import request

            from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

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

    @pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
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
            self.client.set_cookie("localhost", "test-cookie1", "sqlite_master")
            resp = self.client.post("/sqli/cookies/")
            assert resp.status_code == 200

            root_span = self.pop_spans()[0]
            assert root_span.get_metric(IAST.ENABLED) is None

            assert root_span.get_tag(IAST.JSON) is None
