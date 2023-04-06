import mock
import pytest

from ddtrace import Pin
from ddtrace.appsec.iast._util import _is_python_version_supported
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.settings import Config
from ddtrace.settings.integration import IntegrationConfig
from tests.utils import TracerTestCase


class TestTracedCursor(TracerTestCase):
    def setUp(self):
        from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
        from ddtrace.appsec.iast._taint_tracking import setup

        super(TestTracedCursor, self).setUp()
        self.cursor = mock.Mock()
        setattr(self.cursor.execute, "__name__", "execute")

        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

    @pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
    def test_tainted_query(self):
        from ddtrace.appsec.iast._input_info import Input_info
        from ddtrace.appsec.iast._taint_tracking import taint_pyobject

        with mock.patch("ddtrace.contrib.dbapi._is_iast_enabled", return_value=True), mock.patch(
            "ddtrace.appsec.iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            query = "SELECT * FROM db;"
            query = taint_pyobject(query, Input_info("query", query, 0))

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, Pin("dbapi_service", tracer=self.tracer), cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_called_once_with(evidence_value=query)

    @pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
    def test_tainted_query_args(self):
        from ddtrace.appsec.iast._input_info import Input_info
        from ddtrace.appsec.iast._taint_tracking import taint_pyobject

        with mock.patch("ddtrace.contrib.dbapi._is_iast_enabled", return_value=True), mock.patch(
            "ddtrace.appsec.iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            query = "SELECT ? FROM db;"
            query_arg = "something"
            query_arg = taint_pyobject(query_arg, Input_info("query_arg", query_arg, 0))

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, Pin("dbapi_service", tracer=self.tracer), cfg)
            traced_cursor.execute(query, (query_arg,))
            cursor.execute.assert_called_once_with(query, (query_arg,))

            mock_sql_injection_report.assert_not_called()

    @pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
    def test_untainted_query(self):
        with mock.patch("ddtrace.contrib.dbapi._is_iast_enabled", return_value=True), mock.patch(
            "ddtrace.appsec.iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            query = "SELECT * FROM db;"

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, Pin("dbapi_service", tracer=self.tracer), cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_not_called()

    @pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
    def test_untainted_query_and_args(self):
        with mock.patch("ddtrace.contrib.dbapi._is_iast_enabled", return_value=True), mock.patch(
            "ddtrace.appsec.iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            query = "SELECT ? FROM db;"
            query_arg = "something"

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, Pin("dbapi_service", tracer=self.tracer), cfg)
            traced_cursor.execute(query, (query_arg,))
            cursor.execute.assert_called_once_with(query, (query_arg,))

            mock_sql_injection_report.assert_not_called()

    @pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
    def test_tainted_query_iast_disabled(self):
        from ddtrace.appsec.iast._input_info import Input_info
        from ddtrace.appsec.iast._taint_tracking import taint_pyobject

        with mock.patch("ddtrace.contrib.dbapi._is_iast_enabled", return_value=False), mock.patch(
            "ddtrace.appsec.iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            query = "SELECT * FROM db;"
            query = taint_pyobject(query, Input_info("query", query, 0))

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, Pin("dbapi_service", tracer=self.tracer), cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_not_called()
