import pyodbc

from ddtrace.contrib.internal.pyodbc.patch import patch
from ddtrace.contrib.internal.pyodbc.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.trace import Pin
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured


PYODBC_CONNECT_DSN = "driver=SQLite3;database=:memory:;"


class PyODBCTest(object):
    """pyodbc test case reuses the connection across tests"""

    conn = None

    def setUp(self):
        super(PyODBCTest, self).setUp()
        patch()

    def tearDown(self):
        super(PyODBCTest, self).tearDown()
        if self.conn:
            try:
                self.conn.close()
            except pyodbc.ProgrammingError:
                pass
        unpatch()

    def _get_conn_tracer(self):
        pass

    def test_simple_query(self):
        conn, tracer = self._get_conn_tracer()

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()
        assert len(spans) == 1

        span = spans[0]
        assert_is_measured(span)
        assert span.service == "pyodbc"
        assert span.name == "pyodbc.query"
        assert span.span_type == "sql"
        assert span.error == 0
        assert span.get_tag("component") == "pyodbc"
        assert span.get_tag("span.kind") == "client"

    def test_simple_query_fetchall(self):
        with self.override_config("pyodbc", dict(trace_fetch_methods=True)):
            conn, tracer = self._get_conn_tracer()

            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
            spans = tracer.pop()
            assert len(spans) == 1

            span = spans[0]
            assert_is_measured(span)
            assert span.service == "pyodbc"
            assert span.name == "pyodbc.query"
            assert span.span_type == "sql"
            assert span.error == 0
            fetch_span = spans[0]
            assert fetch_span.name == "pyodbc.query"
            assert span.get_tag("component") == "pyodbc"
            assert span.get_tag("span.kind") == "client"

    def test_query_with_several_rows(self):
        conn, tracer = self._get_conn_tracer()

        cursor = conn.cursor()
        query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 3
        spans = tracer.pop()
        assert len(spans) == 1
        self.assertEqual(spans[0].name, "pyodbc.query")

    def test_query_with_several_rows_fetchall(self):
        with self.override_config("pyodbc", dict(trace_fetch_methods=True)):
            conn, tracer = self._get_conn_tracer()

            cursor = conn.cursor()
            query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
            cursor.execute(query)
            rows = cursor.fetchall()
            assert len(rows) == 3
            spans = tracer.pop()
            assert len(spans) == 1

            fetch_span = spans[0]
            assert fetch_span.name == "pyodbc.query"

    def test_query_many(self):
        # tests that the executemany method is correctly wrapped.
        conn, tracer = self._get_conn_tracer()

        tracer.enabled = False
        cursor = conn.cursor()

        tracer.enabled = True
        cursor.execute(
            """
            create table if not exists dummy (
                dummy_key VARCHAR(32) PRIMARY KEY,
                dummy_value TEXT NOT NULL)"""
        )

        stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (?, ?), (?, ?)"
        data = ["foo", "this is foo", "bar", "this is bar"]
        cursor.execute(stmt, data)

        query = "SELECT dummy_key, dummy_value FROM dummy ORDER BY dummy_key"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "bar"
        assert rows[0][1] == "this is bar"
        assert rows[1][0] == "foo"
        assert rows[1][1] == "this is foo"

        spans = tracer.pop()
        assert len(spans) == 3
        cursor.execute("drop table if exists dummy")

    def test_query_many_fetchall(self):
        with self.override_config("pyodbc", dict(trace_fetch_methods=True)):
            # tests that the executemany method is correctly wrapped.
            conn, tracer = self._get_conn_tracer()

            tracer.enabled = False
            cursor = conn.cursor()

            tracer.enabled = True
            cursor.execute(
                """
                create table if not exists dummy (
                    dummy_key VARCHAR(32) PRIMARY KEY,
                    dummy_value TEXT NOT NULL)"""
            )

            stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (?, ?)"
            data = [("foo", "this is foo"), ("bar", "this is bar")]
            cursor.executemany(stmt, data)
            query = "SELECT dummy_key, dummy_value FROM dummy ORDER BY dummy_key"
            cursor.execute(query)
            rows = cursor.fetchall()
            assert len(rows) == 2
            assert rows[0][0] == "bar"
            assert rows[0][1] == "this is bar"
            assert rows[1][0] == "foo"
            assert rows[1][1] == "this is foo"

            spans = tracer.pop()
            assert len(spans) == 3
            cursor.execute("drop table if exists dummy")

            fetch_span = spans[2]
            assert fetch_span.name == "pyodbc.query"

    def test_commit(self):
        conn, tracer = self._get_conn_tracer()

        conn.commit()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.get_tag("component") == "pyodbc"
        assert span.get_tag("span.kind") == "client"
        assert span.service == "pyodbc"
        assert span.name == "pyodbc.connection.commit"

    def test_rollback(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.get_tag("component") == "pyodbc"
        assert span.get_tag("span.kind") == "client"
        assert span.service == "pyodbc"
        assert span.name == "pyodbc.connection.rollback"

    def test_context_manager(self):
        conn, tracer = self._get_conn_tracer()
        with conn as conn2:
            with conn2.cursor() as cursor:
                cursor.execute("SELECT 1")
                rows = cursor.fetchall()
                assert len(rows) == 1
            spans = tracer.pop()
            assert len(spans) == 1


class TestPyODBCPatch(PyODBCTest, TracerTestCase):
    def _get_conn_tracer(self):
        if not self.conn:
            self.conn = pyodbc.connect(PYODBC_CONNECT_DSN)
            # Ensure that the default pin is there, with its default value
            pin = Pin.get_from(self.conn)
            assert pin
            # Customize the service
            # we have to apply it on the existing one since new one won't inherit `app`
            pin._clone(tracer=self.tracer).onto(self.conn)

            return self.conn, self.tracer

    def test_patch_unpatch(self):
        unpatch()
        # assert we start unpatched
        conn = pyodbc.connect(PYODBC_CONNECT_DSN)
        assert not Pin.get_from(conn)
        conn.close()

        patch()
        try:
            conn = pyodbc.connect(PYODBC_CONNECT_DSN)
            pin = Pin.get_from(conn)
            assert pin
            pin._clone(tracer=self.tracer).onto(conn)

            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
            spans = self.pop_spans()
            assert len(spans) == 1

            span = spans[0]
            assert span.service == "pyodbc"
            assert span.name == "pyodbc.query"
            assert span.span_type == "sql"
            assert span.get_tag("db.system") == "SQLite"
            assert span.get_tag("db.user") == ""
            assert span.error == 0
        finally:
            unpatch()

            # assert we finish unpatched
            conn = pyodbc.connect(PYODBC_CONNECT_DSN)
            assert not Pin.get_from(conn)
            conn.close()

        patch()

    def test_user_pin_override(self):
        conn, tracer = self._get_conn_tracer()
        pin = Pin.get_from(conn)
        pin._clone(service="pin-svc", tracer=self.tracer).onto(conn)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()
        assert len(spans) == 1

        span = spans[0]
        assert span.service == "pin-svc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_PYODBC_SERVICE="my-pyodbc-service"))
    def test_user_specified_service_integration(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "my-pyodbc-service"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_schematized_service_name_default(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "pyodbc", "Expected service name to be 'pyodbc' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_service_name_v0(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "pyodbc", "Expected service name to be 'pyodbc' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_service_name_v1(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc", "Expected service name to be 'mysvc' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_schematized_unspecified_service_name_default(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "pyodbc", "Expected service name to be 'pyodbc' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_unspecified_service_name_v0(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "pyodbc", "Expected service name to be 'pyodbc' but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_unspecified_service_name_v1(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert (
            span.service == DEFAULT_SPAN_SERVICE_NAME
        ), "Expected service name to be internal.schema.DEFAULT_SPAN_SERVICE_NAME but was '{}'".format(span.service)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_schematized_operation_name_v0(self):
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()

        cursor.execute("SELECT 1")
        cursor.fetchall()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "pyodbc.query", "Expected operation name to be 'pyodbc.query' but was '{}'".format(
            span.name
        )

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_schematized_operation_name_v1(self):
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()

        cursor.execute("SELECT 1")
        cursor.fetchall()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "pyodbc.query", "Expected operation name to be 'pyodbc.query' but was '{}'".format(
            span.name
        )
