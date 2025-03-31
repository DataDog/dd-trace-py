import mock
import pytest

from ddtrace.appsec._iast import oce
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.settings._config import Config
from ddtrace.settings.asm import config as asm_config
from ddtrace.settings.integration import IntegrationConfig
from ddtrace.trace import Pin
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.utils import TracerTestCase
from tests.utils import override_global_config


class TestTracedCursor(TracerTestCase):
    def setUp(self):
        super(TestTracedCursor, self).setUp()
        with override_global_config(
            dict(
                _iast_enabled=True,
                _iast_deduplication_enabled=False,
                _iast_request_sampling=100.0,
            )
        ):
            _start_iast_context_and_oce()
        self.cursor = mock.Mock()
        self.cursor.execute.__name__ = "execute"

    def tearDown(self):
        with override_global_config(
            dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
        ):
            _end_iast_context_and_oce()

    @pytest.mark.skipif(not asm_config._iast_supported, reason="IAST compatible versions")
    def test_tainted_query(self):
        from ddtrace.appsec._iast._taint_tracking import OriginType
        from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

        with override_global_config(
            dict(
                _iast_enabled=True,
            )
        ), mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            query = "SELECT * FROM db;"
            query = taint_pyobject(query, source_name="query", source_value=query, source_origin=OriginType.PARAMETER)

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            pin = Pin("dbapi_service")
            pin._tracer = self.tracer
            traced_cursor = TracedCursor(cursor, pin, cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_called_once_with(evidence_value=query, dialect="sqlite")

    @pytest.mark.skipif(not asm_config._iast_supported, reason="IAST compatible versions")
    def test_tainted_query_args(self):
        from ddtrace.appsec._iast._taint_tracking import OriginType
        from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

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
            pin = Pin("dbapi_service")
            pin._tracer = self.tracer
            traced_cursor = TracedCursor(cursor, pin, cfg)
            traced_cursor.execute(query, (query_arg,))
            cursor.execute.assert_called_once_with(query, (query_arg,))

            mock_sql_injection_report.assert_not_called()

    @pytest.mark.skipif(not asm_config._iast_supported, reason="IAST compatible versions")
    def test_untainted_query(self):
        with mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            query = "SELECT * FROM db;"

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            pin = Pin("dbapi_service")
            pin._tracer = self.tracer
            traced_cursor = TracedCursor(cursor, pin, cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_not_called()

    @pytest.mark.skipif(not asm_config._iast_supported, reason="IAST compatible versions")
    def test_untainted_query_and_args(self):
        with mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            query = "SELECT ? FROM db;"
            query_arg = "something"

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            pin = Pin("dbapi_service")
            pin._tracer = self.tracer
            traced_cursor = TracedCursor(cursor, pin, cfg)
            traced_cursor.execute(query, (query_arg,))
            cursor.execute.assert_called_once_with(query, (query_arg,))

            mock_sql_injection_report.assert_not_called()

    @pytest.mark.skipif(not asm_config._iast_supported, reason="IAST compatible versions")
    def test_tainted_query_iast_disabled(self):
        from ddtrace.appsec._iast._taint_tracking import OriginType
        from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject

        with mock.patch(
            "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
        ) as mock_sql_injection_report:
            oce._enabled = True
            query = "SELECT * FROM db;"
            query = taint_pyobject(query, source_name="query", source_value=query, source_origin=OriginType.PARAMETER)

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            pin = Pin("dbapi_service")
            pin._tracer = self.tracer
            traced_cursor = TracedCursor(cursor, pin, cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_not_called()
