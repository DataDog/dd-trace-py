import mock
import pytest

from ddtrace import Pin
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking import taint_pyobject
from ddtrace.appsec._iast._utils import _is_python_version_supported
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.settings import Config
from ddtrace.settings.integration import IntegrationConfig
from ddtrace.settings.asm import config as asm_config
from tests.utils import TracerTestCase
from tests.utils import override_env


class TestTracedCursor(TracerTestCase):
    def setUp(self):
        super(TestTracedCursor, self).setUp()
        from ddtrace.appsec._iast._taint_tracking import create_context

        create_context()
        self.cursor = mock.Mock()
        self.cursor.execute.__name__ = "execute"

    def tearDown(self):
        from ddtrace.appsec._iast._taint_tracking import reset_context

        reset_context()
        asm_config._iast_enabled = False

    @pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
    def test_tainted_query(self):
        asm_config._iast_enabled = True
        with mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            oce._enabled = True
            query = "SELECT * FROM db;"
            query = taint_pyobject(query, source_name="query", source_value=query, source_origin=OriginType.PARAMETER)

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, Pin("dbapi_service", tracer=self.tracer), cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_called_once_with(evidence_value=query, dialect="sqlite")

    @pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
    def test_tainted_query_args(self):
        asm_config._iast_enabled = True
        with mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            oce._enabled = True
            query = "SELECT ? FROM db;"
            query_arg = "something"
            query_arg = taint_pyobject(
                query_arg, source_name="query_arg", source_value=query_arg, source_origin=OriginType.PARAMETER
            )

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, Pin("dbapi_service", tracer=self.tracer), cfg)
            traced_cursor.execute(query, (query_arg,))
            cursor.execute.assert_called_once_with(query, (query_arg,))

            mock_sql_injection_report.assert_not_called()

    @pytest.mark.skipif(not _is_python_version_supported(), reason="IAST compatible versions")
    def test_untainted_query(self):
        asm_config._iast_enabled = True
        with mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
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
        asm_config._iast_enabled = True
        with mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
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
        with mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            oce._enabled = True
            query = "SELECT * FROM db;"
            query = taint_pyobject(query, source_name="query", source_value=query, source_origin=OriginType.PARAMETER)

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, Pin("dbapi_service", tracer=self.tracer), cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_not_called()
