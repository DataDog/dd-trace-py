from contextlib import closing
import sqlite3
from unittest import mock

import pytest

from ddtrace.appsec._iast import load_iast
from ddtrace.appsec._iast._overhead_control_engine import oce
from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.contrib.internal.sqlite3.patch import TracedSQLite
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.settings.standalone import standalone_config
from tests.appsec.iast.iast_utils import _end_iast_context_and_oce
from tests.appsec.iast.iast_utils import _get_iast_data
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce
from tests.utils import TracerTestCase
from tests.utils import override_global_config


@pytest.mark.parametrize("owner", ["connection", "cursor"])
@pytest.mark.parametrize("method", ["execute", "executemany", "executescript"])
@pytest.mark.parametrize("tainted", [False, True])
def test_sqlite_query_reports(iast_context_defaults, owner, method, tainted):
    load_iast()
    with closing(sqlite3.Connection(":memory:")) as raw:
        raw.execute("CREATE TABLE items (name TEXT)")
        connection = TracedSQLite(raw)
        target = connection if owner == "connection" else connection.cursor()
        query = "INSERT INTO items VALUES ('alice')"
        if tainted:
            query = taint_pyobject(query, source_name="query", source_value=query, source_origin=OriginType.PARAMETER)
        args = (query, iter([()])) if method == "executemany" else (query,)
        getattr(target, method)(*args)
        assert raw.execute("SELECT name FROM items").fetchall() == [("alice",)]

    report = _get_iast_data()
    if tainted:
        assert len(report["vulnerabilities"]) == 1
        assert report["vulnerabilities"][0]["type"] == VULN_SQL_INJECTION
    else:
        assert not report


@pytest.mark.parametrize("method", ["execute", "executemany"])
def test_sqlite_bound_parameters_do_not_report(iast_context_defaults, method):
    load_iast()
    value = "alice' OR 1=1 -- "
    value = taint_pyobject(value, source_name="name", source_value=value, source_origin=OriginType.PARAMETER)
    with closing(sqlite3.Connection(":memory:")) as raw:
        raw.execute("CREATE TABLE items (name TEXT)")
        connection = TracedSQLite(raw)
        parameters = (value,) if method == "execute" else iter([(value,)])
        getattr(connection, method)("INSERT INTO items VALUES (?)", parameters)
        assert raw.execute("SELECT name FROM items").fetchall() == [(value,)]
    assert not _get_iast_data()


@pytest.mark.parametrize("owner", ["connection", "cursor"])
@pytest.mark.parametrize("method", ["execute", "executemany", "executescript"])
def test_sqlite_reports_in_standalone_mode(iast_context_defaults, tracer, monkeypatch, owner, method):
    load_iast()
    monkeypatch.setattr(tracer, "enabled", False)
    monkeypatch.setattr(standalone_config, "apm_tracing_enabled", False)
    query = "INSERT INTO items VALUES ('alice')"
    query = taint_pyobject(query, source_name="query", source_value=query, source_origin=OriginType.PARAMETER)
    with closing(sqlite3.Connection(":memory:")) as raw:
        raw.execute("CREATE TABLE items (name TEXT)")
        connection = TracedSQLite(raw)
        target = connection if owner == "connection" else connection.cursor()
        args = (query, iter([()])) if method == "executemany" else (query,)
        getattr(target, method)(*args)
        assert raw.execute("SELECT name FROM items").fetchall() == [("alice",)]
    report = _get_iast_data()
    assert len(report["vulnerabilities"]) == 1
    assert report["vulnerabilities"][0]["type"] == VULN_SQL_INJECTION


class TestTracedCursor(TracerTestCase):
    def setUp(self):
        super().setUp()
        load_iast()
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

        with (
            override_global_config(
                dict(
                    _iast_enabled=True,
                )
            ),
            mock.patch(
                "ddtrace.appsec._iast.taint_sinks.sql_injection.SqlInjection.report"
            ) as mock_sql_injection_report,
        ):
            query = "SELECT * FROM db;"
            query = taint_pyobject(query, source_name="query", source_value=query, source_origin=OriginType.PARAMETER)

            cursor = self.cursor
            cfg = IntegrationConfig(Config(), "sqlite", service="dbapi_service")
            traced_cursor = TracedCursor(cursor, cfg=cfg)
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
            traced_cursor = TracedCursor(cursor, cfg=cfg)
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
            traced_cursor = TracedCursor(cursor, cfg=cfg)
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
            traced_cursor = TracedCursor(cursor, cfg=cfg)
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
            traced_cursor = TracedCursor(cursor, cfg=cfg)
            traced_cursor.execute(query)
            cursor.execute.assert_called_once_with(query)

            mock_sql_injection_report.assert_not_called()
