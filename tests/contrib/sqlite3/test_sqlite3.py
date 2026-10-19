from __future__ import annotations

from contextlib import closing
import sys


try:
    sys.modules["sqlite3"] = __import__("pysqlite3")
except ImportError:
    pass

import sqlite3
import time
from typing import TYPE_CHECKING

import pytest

import ddtrace
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib.internal.sqlite3.patch import TracedSQLiteCursor
from ddtrace.contrib.internal.sqlite3.patch import patch
from ddtrace.contrib.internal.sqlite3.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.schema.default import DEFAULT_SPAN_SERVICE_NAME
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_is_not_measured
from tests.utils import override_config


if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import Generator


@pytest.fixture
def patched_conn() -> Generator[sqlite3.Connection, None, None]:
    was_patched = getattr(sqlite3, "_datadog_patch", False)
    patch()
    conn = sqlite3.connect(":memory:")
    try:
        yield conn
    finally:
        conn.close()
        if not was_patched:
            unpatch()


class TestSQLite(TracerTestCase):
    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        unpatch()
        super().tearDown()

    def test_service_info(self):
        backup_tracer = ddtrace.tracer
        ddtrace.tracer = self.tracer

        sqlite3.connect(":memory:")

        ddtrace.tracer = backup_tracer

    def test_sqlite(self):
        db = sqlite3.connect(":memory:")
        # Ensure we can run a query and it's correctly traced
        q = "select * from sqlite_master"
        start = time.time()
        cursor = db.execute(q)
        self.assertIsInstance(cursor, TracedSQLiteCursor)
        rows = cursor.fetchall()
        end = time.time()
        assert not rows
        self.assert_structure(
            dict(name="sqlite.query", span_type="sql", resource=q, error=0),
        )
        root = self.get_root_span()
        assert_is_measured(root)
        self.assertIsNone(root.get_tag("sql.query"))
        self.assertEqual(root.get_tag("component"), "sqlite")
        self.assertEqual(root.get_tag("span.kind"), "client")
        self.assertEqual(root.get_tag("db.system"), "sqlite")
        assert start <= root.start <= end
        assert root.duration <= end - start
        self.reset()

        # run a query with an error and ensure all is well
        q = "select * from some_non_existant_table"
        with pytest.raises(sqlite3.OperationalError):
            db.execute(q)

        self.assert_structure(
            dict(name="sqlite.query", span_type="sql", resource=q, error=1),
        )
        root = self.get_root_span()
        assert_is_measured(root)
        self.assertIsNone(root.get_tag("sql.query"))
        self.assertEqual(root.get_tag("component"), "sqlite")
        self.assertEqual(root.get_tag("span.kind"), "client")
        self.assertEqual(root.get_tag("db.system"), "sqlite")
        self.assertIsNotNone(root.get_tag(ERROR_STACK))
        self.assertIn("OperationalError", root.get_tag(ERROR_TYPE))
        self.assertIn("no such table", root.get_tag(ERROR_MSG))
        self.reset()

    def test_sqlite_fetchall_is_traced(self):
        q = "select * from sqlite_master"

        # Not traced by default
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchall()
        self.assert_structure(dict(name="sqlite.query", resource=q))
        self.reset()

        with self.override_config("sqlite", dict(trace_fetch_methods=True)):
            connection = self._given_a_traced_connection(self.tracer)
            cursor = connection.execute(q)
            cursor.fetchall()

            # We have two spans side by side
            query_span, fetchall_span = self.get_root_spans()

            # Assert query
            query_span.assert_structure(dict(name="sqlite.query", resource=q))
            assert_is_measured(query_span)

            # Assert fetchall
            fetchall_span.assert_structure(dict(name="sqlite.query.fetchall", resource=q, span_type="sql", error=0))
            assert_is_not_measured(fetchall_span)
            self.assertIsNone(fetchall_span.get_tag("sql.query"))

    def test_sqlite_fetchone_is_traced(self):
        q = "select * from sqlite_master"

        # Not traced by default
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchone()
        self.assert_structure(dict(name="sqlite.query", resource=q))
        self.reset()

        with self.override_config("sqlite", dict(trace_fetch_methods=True)):
            connection = self._given_a_traced_connection(self.tracer)
            cursor = connection.execute(q)
            cursor.fetchone()

            # We have two spans side by side
            query_span, fetchone_span = self.get_root_spans()

            # Assert query
            assert_is_measured(query_span)
            self.assertEqual(query_span.get_tag("db.system"), "sqlite")
            query_span.assert_structure(dict(name="sqlite.query", resource=q))

            # Assert fetchone
            assert_is_not_measured(fetchone_span)
            fetchone_span.assert_structure(
                dict(
                    name="sqlite.query.fetchone",
                    resource=q,
                    span_type="sql",
                    error=0,
                ),
            )
            self.assertEqual(fetchone_span.get_tag("db.system"), "sqlite")
            self.assertIsNone(fetchone_span.get_tag("sql.query"))

    def test_sqlite_fetchmany_is_traced(self):
        q = "select * from sqlite_master"

        # Not traced by default
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchmany(123)
        self.assert_structure(dict(name="sqlite.query", resource=q))
        self.reset()

        with self.override_config("sqlite", dict(trace_fetch_methods=True)):
            connection = self._given_a_traced_connection(self.tracer)
            cursor = connection.execute(q)
            cursor.fetchmany(123)

            # We have two spans side by side
            query_span, fetchmany_span = self.get_root_spans()

            # Assert query
            assert_is_measured(query_span)
            query_span.assert_structure(dict(name="sqlite.query", resource=q))
            self.assertEqual(query_span.get_tag("db.system"), "sqlite")

            # Assert fetchmany
            assert_is_not_measured(fetchmany_span)
            fetchmany_span.assert_structure(
                dict(
                    name="sqlite.query.fetchmany",
                    resource=q,
                    span_type="sql",
                    error=0,
                    metrics={"db.fetch.size": 123},
                ),
            )
            self.assertIsNone(fetchmany_span.get_tag("sql.query"))
            self.assertEqual(fetchmany_span.get_tag("db.system"), "sqlite")

    def test_commit(self):
        connection = self._given_a_traced_connection(self.tracer)
        connection.commit()
        self.assertEqual(len(self.spans), 1)
        span = self.spans[0]
        self.assertEqual(span.service, "sqlite")
        self.assertEqual(span.name, "sqlite.connection.commit")

    def test_rollback(self):
        connection = self._given_a_traced_connection(self.tracer)
        connection.rollback()
        self.assert_structure(
            dict(name="sqlite.connection.rollback", service="sqlite"),
        )

    @pytest.mark.snapshot()
    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        db = sqlite3.connect(":memory:")
        db.cursor().execute("select 'blah'").fetchall()

        self.assert_structure(
            dict(name="sqlite.query"),
        )
        self.reset()

        # Test unpatch
        unpatch()

        db = sqlite3.connect(":memory:")
        db.cursor().execute("select 'blah'").fetchall()

        self.assert_has_no_spans()

        # Test patch again
        patch()

        db = sqlite3.connect(":memory:")
        db.cursor().execute("select 'blah'").fetchall()

        self.assert_structure(
            dict(name="sqlite.query"),
        )

    def _given_a_traced_connection(self, tracer):
        db = sqlite3.connect(":memory:")
        return db

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_app_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The sqlite3 integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        q = "select * from sqlite_master"
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchall()

        spans = self.get_spans()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert span.service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_app_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The sqlite3 integration should use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        q = "select * from sqlite_master"
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchall()

        spans = self.get_spans()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SQLITE_SERVICE="my-svc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_user_specified_service_v0(self):
        q = "select * from sqlite_master"
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchall()

        spans = self.get_spans()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert span.service == "my-svc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_SQLITE_SERVICE="my-svc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_user_specified_service_v1(self):
        q = "select * from sqlite_master"
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchall()

        spans = self.get_spans()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert span.service == "my-svc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1(self):
        q = "select * from sqlite_master"
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchall()

        spans = self.get_spans()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_span_name_v0_schema(self):
        q = "select * from sqlite_master"
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchall()

        spans = self.get_spans()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert span.name == "sqlite.query"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_span_name_v1_schema(self):
        q = "select * from sqlite_master"
        connection = self._given_a_traced_connection(self.tracer)
        cursor = connection.execute(q)
        cursor.fetchall()

        spans = self.get_spans()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        assert span.name == "sqlite.query"

    def test_context_manager(self):
        conn = self._given_a_traced_connection(self.tracer)
        with conn as conn2:
            cursor = conn2.execute("select * from sqlite_master")
            cursor.fetchall()
            cursor.fetchall()
            spans = self.get_spans()
            assert len(spans) == 1


def test_iterator_usage(patched_conn):
    """Ensure sqlite3 patched cursors can be used as iterators."""
    rows = next(patched_conn.execute("select 1"))
    assert len(rows) == 1


def test_backup(patched_conn):
    """Ensure sqlite3 patched connections backup function can be used"""
    destination = sqlite3.connect(":memory:")

    with destination:
        patched_conn.backup(destination, pages=1)


@pytest.mark.parametrize("trace_fetch_methods", [False, True])
@pytest.mark.parametrize("owner", ["connection", "cursor"])
@pytest.mark.parametrize("method", ["execute", "executemany", "executescript"])
def test_execution_entrypoints(tracer, test_spans, trace_fetch_methods, owner, method):
    was_patched = getattr(sqlite3, "_datadog_patch", False)
    with override_config("sqlite", {"trace_fetch_methods": trace_fetch_methods}):
        patch()
        connection = sqlite3.connect(":memory:")
    raw = connection.__wrapped__
    raw.execute("CREATE TABLE items (name TEXT)")
    target = connection if owner == "connection" else connection.cursor()
    events = []
    listener = events.append
    core.on(DbQueryEvent.event_name, listener)
    try:
        if method == "executescript":
            query = "INSERT INTO items VALUES ('alice'); INSERT INTO items VALUES ('bob');"
            args = (query,)
        else:
            query = "INSERT INTO items VALUES (?)"
            parameters = ("alice",) if method == "execute" else iter([("alice",), ("bob",)])
            args = (query, parameters)
        cursor = getattr(target, method)(*args)
        assert isinstance(cursor, TracedSQLiteCursor)
        assert cursor.connection is raw
        if owner == "cursor":
            assert cursor is target
        assert len(events) == 1
        assert events[0].query is query
        assert events[0].span_name_prefix == "sqlite"
        spans = test_spans.spans
        assert len(spans) == 1
        assert spans[0].resource == query
        assert spans[0].name == "sqlite.query"
        assert spans[0].get_tag("db.system") == "sqlite"
        assert cursor.fetchall() == []
        assert len(test_spans.spans) == (2 if trace_fetch_methods else 1)
        if trace_fetch_methods:
            assert test_spans.spans[-1].name == "sqlite.query.fetchall"
            assert test_spans.spans[-1].resource == query
        assert len(events) == 1
        assert raw.execute("SELECT name FROM items ORDER BY name").fetchall() == (
            [("alice",)] if method == "execute" else [("alice",), ("bob",)]
        )
    finally:
        core.reset_listeners(DbQueryEvent.event_name, listener)
        connection.close()
        if not was_patched:
            unpatch()


@pytest.mark.parametrize("owner", ["connection", "cursor"])
def test_executescript_preserves_errors_and_partial_execution(patched_conn, owner):
    target = patched_conn if owner == "connection" else patched_conn.cursor()
    with pytest.raises(sqlite3.OperationalError):
        target.executescript("CREATE TABLE created (value); INSERT INTO missing VALUES (1);")
    assert patched_conn.__wrapped__.execute("SELECT COUNT(*) FROM created").fetchone() == (0,)


def test_executescript_preserves_implicit_commit(patched_conn):
    raw = patched_conn.__wrapped__
    raw.execute("CREATE TABLE items (value)")
    raw.execute("INSERT INTO items VALUES (1)")
    assert raw.in_transaction
    patched_conn.executescript("INSERT INTO items VALUES (2);")
    assert not raw.in_transaction
    raw.rollback()
    assert raw.execute("SELECT value FROM items ORDER BY value").fetchall() == [(1,), (2,)]


@pytest.mark.parametrize("script", [None, 1, b"SELECT 1;"])
@pytest.mark.parametrize("owner", ["connection", "cursor"])
def test_executescript_preserves_argument_errors(patched_conn, script, owner):
    raw = patched_conn.__wrapped__
    native_target = raw if owner == "connection" else raw.cursor()
    traced_target = patched_conn if owner == "connection" else patched_conn.cursor()
    with pytest.raises((TypeError, ValueError)) as native_error:
        native_target.executescript(script)
    with pytest.raises(type(native_error.value)) as traced_error:
        traced_target.executescript(script)
    assert str(traced_error.value) == str(native_error.value)


def test_cursor_factory_and_connection(patched_conn):
    class CustomCursor(sqlite3.Cursor):
        pass

    cursor = patched_conn.cursor(factory=CustomCursor)
    assert isinstance(cursor.__wrapped__, CustomCursor)
    assert cursor.connection is patched_conn.__wrapped__
    assert cursor.executescript("SELECT 1;") is cursor


def test_cursor_connection_preserves_native_type(patched_conn):
    connection = patched_conn.cursor().connection
    assert connection is patched_conn.__wrapped__
    with closing(sqlite3.Cursor(connection)) as cursor:
        assert cursor.execute("SELECT 1").fetchone() == (1,)
    with closing(sqlite3.Connection(":memory:")) as source:
        source.execute("CREATE TABLE copied (value)")
        source.backup(connection)
    assert connection.execute("SELECT COUNT(*) FROM copied").fetchone() == (0,)
