import mock
import pymysql

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.pymysql.patch import patch
from ddtrace.contrib.pymysql.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.contrib import shared_tests
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_dict_issuperset
from tests.utils import assert_is_measured

from ...contrib.config import MYSQL_CONFIG


MYSQL_CONFIG["db"] = MYSQL_CONFIG["database"]


class PyMySQLCore(object):
    """PyMySQL test case reuses the connection across tests"""

    conn = None

    DB_INFO = {
        "out.host": MYSQL_CONFIG.get("host"),
        "db.system": "mysql",
    }
    DB_INFO.update(
        {
            "db.user": str(MYSQL_CONFIG.get("user")),
            "db.name": str(MYSQL_CONFIG.get("database")),
        }
    )

    def setUp(self):
        super(PyMySQLCore, self).setUp()
        patch()

    def tearDown(self):
        super(PyMySQLCore, self).tearDown()
        if self.conn and not self.conn._closed:
            self.conn.close()
        unpatch()

    def _get_conn_tracer(self):
        # implement me
        pass

    def test_simple_query(self):
        conn, tracer = self._get_conn_tracer()

        cursor = conn.cursor()

        # PyMySQL returns back the rowcount instead of a cursor
        rowcount = cursor.execute("SELECT 1")
        assert rowcount == 1

        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()
        assert len(spans) == 1

        span = spans[0]
        assert_is_measured(span)
        assert span.service == "pymysql"
        assert span.name == "pymysql.query"
        assert span.span_type == "sql"
        assert span.error == 0
        assert span.get_metric("network.destination.port") == MYSQL_CONFIG.get("port")
        assert span.get_tag("component") == "pymysql"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "mysql"
        meta = {}
        meta.update(self.DB_INFO)
        assert_dict_issuperset(span.get_tags(), meta)

    def test_simple_query_fetchall(self):
        with self.override_config("pymysql", dict(trace_fetch_methods=True)):
            conn, tracer = self._get_conn_tracer()

            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
            spans = tracer.pop()
            assert len(spans) == 2

            span = spans[0]
            assert_is_measured(span)
            assert span.service == "pymysql"
            assert span.name == "pymysql.query"
            assert span.span_type == "sql"
            assert span.error == 0
            assert span.get_metric("network.destination.port") == MYSQL_CONFIG.get("port")
            assert span.get_tag("component") == "pymysql"
            assert span.get_tag("span.kind") == "client"
            assert span.get_tag("db.system") == "mysql"
            meta = {}
            meta.update(self.DB_INFO)
            assert_dict_issuperset(span.get_tags(), meta)

            fetch_span = spans[1]
            assert fetch_span.name == "pymysql.query.fetchall"

    def test_query_with_several_rows(self):
        conn, tracer = self._get_conn_tracer()

        cursor = conn.cursor()
        query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 3
        spans = tracer.pop()
        assert len(spans) == 1
        self.assertEqual(spans[0].name, "pymysql.query")

    def test_query_with_several_rows_fetchall(self):
        with self.override_config("pymysql", dict(trace_fetch_methods=True)):
            conn, tracer = self._get_conn_tracer()

            cursor = conn.cursor()
            query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
            cursor.execute(query)
            rows = cursor.fetchall()
            assert len(rows) == 3
            spans = tracer.pop()
            assert len(spans) == 2

            fetch_span = spans[1]
            assert fetch_span.name == "pymysql.query.fetchall"

    def test_query_many(self):
        # tests that the executemany method is correctly wrapped.
        conn, tracer = self._get_conn_tracer()

        tracer.enabled = False
        cursor = conn.cursor()

        cursor.execute(
            """
            create table if not exists dummy (
                dummy_key VARCHAR(32) PRIMARY KEY,
                dummy_value TEXT NOT NULL)"""
        )
        tracer.enabled = True

        stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (%s, %s)"
        data = [("foo", "this is foo"), ("bar", "this is bar")]

        # PyMySQL `executemany()` returns the rowcount
        rowcount = cursor.executemany(stmt, data)
        assert rowcount == 2

        query = "SELECT dummy_key, dummy_value FROM dummy ORDER BY dummy_key"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "bar"
        assert rows[0][1] == "this is bar"
        assert rows[1][0] == "foo"
        assert rows[1][1] == "this is foo"

        spans = tracer.pop()
        assert len(spans) == 2
        cursor.execute("drop table if exists dummy")

    def test_query_many_fetchall(self):
        with self.override_config("pymysql", dict(trace_fetch_methods=True)):
            # tests that the executemany method is correctly wrapped.
            conn, tracer = self._get_conn_tracer()

            tracer.enabled = False
            cursor = conn.cursor()

            cursor.execute(
                """
                create table if not exists dummy (
                    dummy_key VARCHAR(32) PRIMARY KEY,
                    dummy_value TEXT NOT NULL)"""
            )
            tracer.enabled = True

            stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (%s, %s)"
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
            assert fetch_span.name == "pymysql.query.fetchall"

    def test_query_proc(self):
        conn, tracer = self._get_conn_tracer()

        # create a procedure
        tracer.enabled = False
        cursor = conn.cursor()
        cursor.execute("DROP PROCEDURE IF EXISTS sp_sum")
        cursor.execute(
            """
            CREATE PROCEDURE sp_sum (IN p1 INTEGER, IN p2 INTEGER, OUT p3 INTEGER)
            BEGIN
                SET p3 := p1 + p2;
            END;"""
        )

        tracer.enabled = True
        proc = "sp_sum"
        data = (40, 2, None)

        # spans[len(spans) - 2]
        cursor.callproc(proc, data)

        # spans[len(spans) - 1]
        cursor.execute(
            """
                       SELECT @_sp_sum_0, @_sp_sum_1, @_sp_sum_2
                       """
        )
        output = cursor.fetchone()
        assert len(output) == 3
        assert output[2] == 42

        spans = tracer.pop()
        assert spans, spans

        # number of spans depends on PyMySQL implementation details,
        # typically, internal calls to execute, but at least we
        # can expect the last closed span to be our proc.
        span = spans[len(spans) - 2]
        assert_is_measured(span)
        assert span.service == "pymysql"
        assert span.name == "pymysql.query"
        assert span.span_type == "sql"
        assert span.error == 0
        assert span.get_metric("network.destination.port") == MYSQL_CONFIG.get("port")
        meta = {}
        meta.update(self.DB_INFO)
        assert_dict_issuperset(span.get_tags(), meta)

    def test_simple_query_ot(self):
        """OpenTracing version of test_simple_query."""
        conn, tracer = self._get_conn_tracer()

        ot_tracer = init_tracer("mysql_svc", tracer)
        with ot_tracer.start_active_span("mysql_op"):
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1

        spans = tracer.pop()
        assert len(spans) == 2
        ot_span, dd_span = spans

        # confirm parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.service == "mysql_svc"
        assert ot_span.name == "mysql_op"

        assert_is_measured(dd_span)
        assert dd_span.service == "pymysql"
        assert dd_span.name == "pymysql.query"
        assert dd_span.span_type == "sql"
        assert dd_span.error == 0
        assert dd_span.get_metric("network.destination.port") == MYSQL_CONFIG.get("port")
        meta = {}
        meta.update(self.DB_INFO)
        assert_dict_issuperset(dd_span.get_tags(), meta)

    def test_simple_query_ot_fetchall(self):
        """OpenTracing version of test_simple_query."""
        with self.override_config("pymysql", dict(trace_fetch_methods=True)):
            conn, tracer = self._get_conn_tracer()

            ot_tracer = init_tracer("mysql_svc", tracer)
            with ot_tracer.start_active_span("mysql_op"):
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                rows = cursor.fetchall()
                assert len(rows) == 1

            spans = tracer.pop()
            assert len(spans) == 3
            ot_span, dd_span, fetch_span = spans

            # confirm parenting
            assert ot_span.parent_id is None
            assert dd_span.parent_id == ot_span.span_id

            assert ot_span.service == "mysql_svc"
            assert ot_span.name == "mysql_op"

            assert_is_measured(dd_span)
            assert dd_span.service == "pymysql"
            assert dd_span.name == "pymysql.query"
            assert dd_span.span_type == "sql"
            assert dd_span.error == 0
            assert dd_span.get_metric("network.destination.port") == MYSQL_CONFIG.get("port")
            meta = {}
            meta.update(self.DB_INFO)
            assert_dict_issuperset(dd_span.get_tags(), meta)

            assert fetch_span.name == "pymysql.query.fetchall"

    def test_commit(self):
        conn, tracer = self._get_conn_tracer()

        conn.commit()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "pymysql"
        assert span.name == "pymysql.connection.commit"

    def test_rollback(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "pymysql"
        assert span.name == "pymysql.connection.rollback"

    def test_analytics_default(self):
        conn, tracer = self._get_conn_tracer()

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()

        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("pymysql", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            conn, tracer = self._get_conn_tracer()

            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
            spans = tracer.pop()

            self.assertEqual(len(spans), 1)
            span = spans[0]
            self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("pymysql", dict(analytics_enabled=True)):
            conn, tracer = self._get_conn_tracer()

            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
            spans = tracer.pop()

            self.assertEqual(len(spans), 1)
            span = spans[0]
            self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)


class TestPyMysqlPatch(PyMySQLCore, TracerTestCase):
    def _get_conn_tracer(self):
        if not self.conn:
            self.conn = pymysql.connect(**MYSQL_CONFIG)
            assert not self.conn._closed
            # Ensure that the default pin is there, with its default value
            pin = Pin.get_from(self.conn)
            assert pin
            # Customize the service
            # we have to apply it on the existing one since new one won't inherit `app`
            pin.clone(tracer=self.tracer).onto(self.conn)

            return self.conn, self.tracer

    def test_patch_unpatch(self):
        unpatch()
        # assert we start unpatched
        conn = pymysql.connect(**MYSQL_CONFIG)
        assert not Pin.get_from(conn)
        conn.close()

        patch()
        try:
            conn = pymysql.connect(**MYSQL_CONFIG)
            pin = Pin.get_from(conn)
            assert pin
            pin.clone(tracer=self.tracer).onto(conn)
            assert not conn._closed

            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
            spans = self.pop_spans()
            assert len(spans) == 1

            span = spans[0]
            assert span.service == "pymysql"
            assert span.name == "pymysql.query"
            assert span.span_type == "sql"
            assert span.error == 0
            assert span.get_metric("network.destination.port") == MYSQL_CONFIG.get("port")

            meta = {}
            meta.update(self.DB_INFO)
            assert_dict_issuperset(span.get_tags(), meta)
        finally:
            unpatch()

            # assert we finish unpatched
            conn = pymysql.connect(**MYSQL_CONFIG)
            assert not Pin.get_from(conn)
            conn.close()

        patch()

    def test_user_pin_override(self):
        conn, tracer = self._get_conn_tracer()
        pin = Pin.get_from(conn)
        pin.clone(service="pin-svc", tracer=self.tracer).onto(conn)
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = tracer.pop()
        assert len(spans) == 1

        span = spans[0]
        assert span.service == "pin-svc"

    def test_context_manager(self):
        conn, tracer = self._get_conn_tracer()
        # connection doesn't support context manager usage
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
        spans = tracer.pop()
        assert len(spans) == 1

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_PYMYSQL_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_user_specified_service_integration_v0(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_PYMYSQL_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_user_specified_service_integration_v1(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "pymysql"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1(self):
        conn, tracer = self._get_conn_tracer()

        conn.rollback()
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_span_name_v0_schema(self):
        conn, tracer = self._get_conn_tracer()

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "pymysql.query"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_span_name_v1_schema(self):
        conn, tracer = self._get_conn_tracer()

        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "mysql.query"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    def test_pymysql_dbm_propagation_enabled(self):
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()

        shared_tests._test_dbm_propagation_enabled(tracer, cursor, "pymysql")

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    def test_pymysql_dbm_propagation_comment_with_global_service_name_configured(self):
        """tests if dbm comment is set in mysql"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        cursor.__wrapped__ = mock.Mock()

        shared_tests._test_dbm_propagation_comment_with_global_service_name_configured(
            config=MYSQL_CONFIG, db_system="pymysql", cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_PYMYSQL_SERVICE="service-name-override",
        )
    )
    def test_pymysql_dbm_propagation_comment_integration_service_name_override(self):
        """tests if dbm comment is set in mysql"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        cursor.__wrapped__ = mock.Mock()

        shared_tests._test_dbm_propagation_comment_integration_service_name_override(
            config=MYSQL_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_PYMYSQL_SERVICE="service-name-override",
        )
    )
    def test_pymysql_dbm_propagation_comment_pin_service_name_override(self):
        """tests if dbm comment is set in mysql"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        cursor.__wrapped__ = mock.Mock()

        shared_tests._test_dbm_propagation_comment_pin_service_name_override(
            config=MYSQL_CONFIG, cursor=cursor, conn=conn, tracer=tracer, wrapped_instance=cursor.__wrapped__
        )

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED="True",
        )
    )
    def test_pymysql_dbm_propagation_comment_peer_service_enabled(self):
        """tests if dbm comment is set in mysql"""
        conn, tracer = self._get_conn_tracer()
        cursor = conn.cursor()
        cursor.__wrapped__ = mock.Mock()

        shared_tests._test_dbm_propagation_comment_peer_service_enabled(
            config=MYSQL_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
        )
