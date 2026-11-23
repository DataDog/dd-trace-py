import ast
from pathlib import Path
from unittest import mock

import pytest

from ddtrace.contrib import dbapi
from ddtrace.internal import core
from ddtrace.internal.database import DatabaseQueryAdapter
from ddtrace.internal.database import DatabaseQueryPrepareEvent
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.propagation import _database_monitoring as dbm


@pytest.fixture
def dbm_hooks(monkeypatch):
    old_active = dbm._active_integrations
    old_legacy = dbm._legacy_listening
    dbm.unlisten()
    with monkeypatch.context() as state:
        state.setattr(dbm, "_active_integrations", set())
        state.setattr(dbm.dbm_config, "propagation_mode", "full")
        yield dbm
        dbm.unlisten()
    dbm._legacy_listening = old_legacy
    if old_active or old_legacy:
        dbm._register_listeners()


def configured_cursor(name="pymysql"):
    cfg = IntegrationConfig(Config(), name, service="orders-db", _query_adapter=DatabaseQueryAdapter(0, "query"))
    raw = mock.Mock(rowcount=1)
    return dbapi.TracedCursor(raw, cfg=cfg), raw


@pytest.mark.parametrize("mode", ["disabled", "service", "full", "dynamic_service"])
def test_optional_dbm_attachment_preserves_driver_calls_and_spans(dbm_hooks, tracer, test_spans, mode):
    dbm_hooks.dbm_config.propagation_mode = mode
    cursor, raw = configured_cursor()
    parameters = object()
    result = object()
    raw.execute.return_value = result
    # The same cursor/config works before attachment and after it, including bytes and parameters.
    assert cursor.execute(b"SELECT 1", parameters) is result
    assert raw.execute.call_args.args == (b"SELECT 1", parameters)
    dbm_hooks.pymysql_hooks.listen()
    with tracer.trace("parent") as parent:
        assert cursor.execute(b"SELECT 1", parameters) is result
    query, actual_parameters = raw.execute.call_args.args
    assert actual_parameters is parameters
    if mode == "disabled":
        assert query == b"SELECT 1"
    else:
        assert query.startswith(b"/*") and query.endswith(b"*/ SELECT 1")
        assert query.count(b"/*") == 1
    spans = [span for span in test_spans.spans if span.name == "sql.query"]
    assert len(spans) == 2
    assert all(span.resource == b"SELECT 1" for span in spans)
    attached = next(span for span in spans if span.parent_id == parent.span_id)
    if mode == "full":
        assert attached.context._traceparent.encode() in query
        assert parent.context._traceparent.encode() not in query
        assert attached.get_tag("_dd.dbm_trace_injected") == "true"
    else:
        assert attached.get_tag("_dd.dbm_trace_injected") is None
    dbm_hooks.pymysql_hooks.unlisten()
    assert cursor.execute(b"SELECT 1", parameters) is result
    assert raw.execute.call_args.args == (b"SELECT 1", parameters)


def test_hook_ownership_is_idempotent_and_preserves_other_consumers(dbm_hooks, tracer, test_spans):
    first, raw_first = configured_cursor("pymysql")
    second, raw_second = configured_cursor("mysql")
    seen = []

    def observe(event):
        seen.append(event)

    core.on(DatabaseQueryPrepareEvent.event_name, observe)
    try:
        dbm_hooks.pymysql_hooks.listen()
        dbm_hooks.pymysql_hooks.listen()
        dbm_hooks.mysql_hooks.listen()
        first.execute("SELECT 1")
        second.execute("SELECT 1")
        assert raw_first.execute.call_args.args[0].startswith("/*")
        assert raw_first.execute.call_args.args[0].count("/*") == 1
        dbm_hooks.pymysql_hooks.unlisten()
        dbm_hooks.pymysql_hooks.unlisten()
        first.execute("SELECT 1")
        second.execute("SELECT 1")
        assert raw_first.execute.call_args.args == ("SELECT 1",)
        assert raw_second.execute.call_args.args[0].startswith("/*")
        assert raw_second.execute.call_args.args[0].count("/*") == 1
        dbm_hooks.mysql_hooks.unlisten()
        second.execute("SELECT 1")
        assert raw_second.execute.call_args.args == ("SELECT 1",)
        dbm_hooks.pymysql_hooks.listen()
        first.execute("SELECT 1")
        assert raw_first.execute.call_args.args[0].startswith("/*")
        assert raw_first.execute.call_args.args[0].count("/*") == 1
        assert len(seen) == 6
        assert len(test_spans.spans) == 6
    finally:
        core.reset_listeners(DatabaseQueryPrepareEvent.event_name, observe)


def test_late_attachment_preserves_explicit_cursor_suppression(dbm_hooks, tracer, test_spans):
    cursor, raw = configured_cursor("django-database")
    cursor._self_dbm_propagator = None
    dbm_hooks.django_hooks.listen()
    cursor.execute("SELECT 1")
    raw.execute.assert_called_once_with("SELECT 1")
    assert cursor._self_config._dbm_propagator is not None
    assert cursor._self_dbm_propagator is None
    # Another cursor using that same cached connection config receives propagation.
    dbapi.TracedCursor(raw, cfg=cursor._self_config).execute("SELECT 2")
    assert raw.execute.call_args.args[0].endswith("*/ SELECT 2")
    assert len(test_spans.spans) == 2


def test_without_dbm_provider_does_not_import_dbm(run_python_code_in_subprocess):
    out, err, status, _ = run_python_code_in_subprocess("""
import importlib.abc
import sqlite3
import sys
from ddtrace.internal import integration_hooks
from ddtrace.internal.database import DatabaseQueryAdapter
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace import config

class RejectDBM(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'ddtrace.propagation._database_monitoring':
            raise AssertionError('instrumentation imported DBM')
sys.meta_path.insert(0, RejectDBM())
integration_hooks._entry_points = {}
integration_hooks.on_patch('pymysql')
from ddtrace.contrib.dbapi import TracedConnection
cfg = IntegrationConfig(config, 'pymysql', trace_fetch_methods=False, _query_adapter=DatabaseQueryAdapter(0, 'query'))
conn = TracedConnection(sqlite3.connect(':memory:'), cfg=cfg)
cursor = conn.cursor()
cursor.execute('select 42')
assert cursor.fetchone() == (42,)
assert 'ddtrace.propagation._database_monitoring' not in sys.modules
integration_hooks.on_unpatch('pymysql')
conn.close()
""")
    assert status == 0, (out, err)


def test_database_contracts_keep_legacy_identity_and_dependency_direction():
    from ddtrace.contrib._events import dbapi as legacy
    from ddtrace.internal import database

    for name in ("DatabaseOperationEvent", "DatabaseQueryPrepareEvent", "DatabaseResultEvent", "DbQueryEvent"):
        assert getattr(legacy, name) is getattr(database, name)
    root = Path(__file__).resolve().parents[3] / "ddtrace"
    paths = [root / "internal/database.py", root / "_trace/subscribers/database.py"]
    paths.extend(
        root / f"contrib/internal/{name}/patch.py"
        for name in ("aiomysql", "asyncpg", "mysql", "mysqldb", "psycopg", "pymongo", "pymysql")
    )
    paths.append(root / "contrib/internal/django/database.py")
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text())):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else ([alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            )
            for module in modules:
                assert "propagation._database_monitoring" not in module, (path, node.lineno)
                if path in paths[:2]:
                    assert not module.startswith("ddtrace.contrib"), (path, node.lineno)
