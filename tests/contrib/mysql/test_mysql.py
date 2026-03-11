from unittest import mock

import mysql

from ddtrace.contrib.internal.mysql.patch import patch
from ddtrace.contrib.internal.mysql.patch import unpatch
from tests.contrib import shared_tests
from tests.contrib.config import MYSQL_CONFIG
from tests.utils import TracerTestCase
from tests.utils import assert_dict_issuperset
from tests.utils import assert_is_measured


MYSQL_CONFIG["db"] = MYSQL_CONFIG["database"]


class MySQLCore(object):
    """Base test case for MySQL drivers"""

    conn = None
    tracer = None

    def tearDown(self):
        super(MySQLCore, self).tearDown()

        # Reuse the connection across tests
        if self.conn:
            try:
                self.conn.ping()
            except mysql.connector.errors.InternalError:
                pass
            except mysql.connector.errors.InterfaceError:
                pass
            else:
                self.conn.close()
        unpatch()

    def _get_conn(self):
        # implement me
        pass

    def test_simple_query(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = self.pop_spans()
        assert len(spans) == 1

        span = spans[0]
        assert_is_measured(span)
        assert span.service == "mysql"
        assert span.name == "mysql.query"
        assert span.span_type == "sql"
        assert span.error == 0
        assert span.get_metric("network.destination.port") == 3306
        assert_dict_issuperset(
            span.get_tags(),
            {
                "out.host": "127.0.0.1",
                "db.name": "test",
                "db.system": "mysql",
                "db.user": "test",
                "component": "mysql",
                "span.kind": "client",
            },
        )

    def test_simple_query_fetchll(self):
        with self.override_config("mysql", dict(trace_fetch_methods=True)):
            conn = self._get_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
            spans = self.pop_spans()
            assert len(spans) == 2

            span = spans[0]
            assert_is_measured(span)
            assert span.service == "mysql"
            assert span.name == "mysql.query"
            assert span.span_type == "sql"
            assert span.error == 0
            assert span.get_metric("network.destination.port") == 3306
            assert_dict_issuperset(
                span.get_tags(),
                {
                    "out.host": "127.0.0.1",
                    "db.name": "test",
                    "db.system": "mysql",
                    "db.user": "test",
                    "component": "mysql",
                    "span.kind": "client",
                },
            )

            assert spans[1].name == "mysql.query.fetchall"

    def test_query_with_several_rows(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 3
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.get_tag("sql.query") is None
        assert span.get_tag("component") == "mysql"
        assert span.get_tag("span.kind") == "client"

    def test_query_with_several_rows_fetchall(self):
        with self.override_config("mysql", dict(trace_fetch_methods=True)):
            conn = self._get_conn()
            cursor = conn.cursor()
            query = "SELECT n FROM (SELECT 42 n UNION SELECT 421 UNION SELECT 4210) m"
            cursor.execute(query)
            rows = cursor.fetchall()
            assert len(rows) == 3
            spans = self.pop_spans()
            assert len(spans) == 2
            span = spans[0]
            assert span.get_tag("sql.query") is None
            assert spans[1].name == "mysql.query.fetchall"
            assert span.get_tag("component") == "mysql"
            assert span.get_tag("span.kind") == "client"

    def test_query_many(self):
        # tests that the executemany method is correctly wrapped.
        conn = self._get_conn()
        self.tracer.enabled = False
        cursor = conn.cursor()

        cursor.execute(
            """
            create table if not exists dummy (
                dummy_key VARCHAR(32) PRIMARY KEY,
                dummy_value TEXT NOT NULL)"""
        )
        self.tracer.enabled = True

        stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (%s, %s)"
        data = [
            ("foo", "this is foo"),
            ("bar", "this is bar"),
        ]
        cursor.executemany(stmt, data)
        query = "SELECT dummy_key, dummy_value FROM dummy ORDER BY dummy_key"
        cursor.execute(query)
        rows = cursor.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "bar"
        assert rows[0][1] == "this is bar"
        assert rows[1][0] == "foo"
        assert rows[1][1] == "this is foo"

        spans = self.pop_spans()
        assert len(spans) == 2
        span = spans[-1]
        assert span.get_tag("sql.query") is None
        assert span.get_tag("component") == "mysql"
        assert span.get_tag("span.kind") == "client"
        cursor.execute("drop table if exists dummy")

    def test_query_many_fetchall(self):
        with self.override_config("mysql", dict(trace_fetch_methods=True)):
            # tests that the executemany method is correctly wrapped.
            conn = self._get_conn()
            self.tracer.enabled = False
            cursor = conn.cursor()

            cursor.execute(
                """
                create table if not exists dummy (
                    dummy_key VARCHAR(32) PRIMARY KEY,
                    dummy_value TEXT NOT NULL)"""
            )
            self.tracer.enabled = True

            stmt = "INSERT INTO dummy (dummy_key, dummy_value) VALUES (%s, %s)"
            data = [
                ("foo", "this is foo"),
                ("bar", "this is bar"),
            ]
            cursor.executemany(stmt, data)
            query = "SELECT dummy_key, dummy_value FROM dummy ORDER BY dummy_key"
            cursor.execute(query)
            rows = cursor.fetchall()
            assert len(rows) == 2
            assert rows[0][0] == "bar"
            assert rows[0][1] == "this is bar"
            assert rows[1][0] == "foo"
            assert rows[1][1] == "this is foo"

            spans = self.pop_spans()
            assert len(spans) == 3
            span = spans[-1]
            assert span.get_tag("sql.query") is None
            assert span.get_tag("component") == "mysql"
            assert span.get_tag("span.kind") == "client"
            cursor.execute("drop table if exists dummy")

            assert spans[2].name == "mysql.query.fetchall"

    def test_query_proc(self):
        conn = self._get_conn()

        # create a procedure
        self.tracer.enabled = False
        cursor = conn.cursor()
        cursor.execute("DROP PROCEDURE IF EXISTS sp_sum")
        cursor.execute(
            """
            CREATE PROCEDURE sp_sum (IN p1 INTEGER, IN p2 INTEGER, OUT p3 INTEGER)
            BEGIN
                SET p3 := p1 + p2;
            END;"""
        )

        self.tracer.enabled = True
        proc = "sp_sum"
        data = (40, 2, None)
        output = cursor.callproc(proc, data)
        assert len(output) == 3
        assert output[2] == 42

        spans = self.pop_spans()
        assert spans, spans

        # number of spans depends on MySQL implementation details,
        # typically, internal calls to execute, but at least we
        # can expect the last closed span to be our proc.
        span = spans[len(spans) - 1]
        assert_is_measured(span)
        assert span.service == "mysql"
        assert span.name == "mysql.query"
        assert span.span_type == "sql"
        assert span.error == 0
        assert span.get_metric("network.destination.port") == 3306
        assert_dict_issuperset(
            span.get_tags(),
            {
                "out.host": "127.0.0.1",
                "db.name": "test",
                "db.system": "mysql",
                "db.user": "test",
                "component": "mysql",
                "span.kind": "client",
            },
        )
        assert span.get_tag("sql.query") is None

    def test_commit(self):
        conn = self._get_conn()
        conn.commit()
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysql"
        assert span.name == "mysql.connection.commit"

    def test_rollback(self):
        conn = self._get_conn()
        conn.rollback()
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysql"
        assert span.name == "mysql.connection.rollback"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The mysql integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = self.pop_spans()

        assert spans[0].service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The mysql integration should use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = self.pop_spans()

        assert spans[0].service == "mysvc"


class TestMysqlPatch(MySQLCore, TracerTestCase):
    def setUp(self):
        super(TestMysqlPatch, self).setUp()
        patch()

    def tearDown(self):
        super(TestMysqlPatch, self).tearDown()
        unpatch()

    def _get_conn(self):
        if not self.conn:
            self.conn = mysql.connector.connect(**MYSQL_CONFIG)
            assert self.conn.is_connected()

        return self.conn

    def test_patch_unpatch(self):
        unpatch()
        # assert we start unpatched
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        conn.close()

        patch()
        try:
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            assert conn.is_connected()

            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            rows = cursor.fetchall()
            assert len(rows) == 1
            spans = self.pop_spans()
            assert len(spans) == 1

            span = spans[0]
            assert span.service == "mysql"
            assert span.name == "mysql.query"
            assert span.span_type == "sql"
            assert span.error == 0
            assert span.get_metric("network.destination.port") == 3306
            assert_dict_issuperset(
                span.get_tags(),
                {
                    "out.host": "127.0.0.1",
                    "db.name": "test",
                    "db.system": "mysql",
                    "db.user": "test",
                    "component": "mysql",
                    "span.kind": "client",
                },
            )
            assert span.get_tag("sql.query") is None

        finally:
            unpatch()

            # assert we finish unpatched
            conn = mysql.connector.connect(**MYSQL_CONFIG)
            conn.close()

        patch()

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_MYSQL_SERVICE="mysvc", DD_TRANCE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_user_specified_service_integration_v0(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = self.pop_spans()

        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_MYSQL_SERVICE="mysvc", DD_TRANCE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_user_specified_service_integration_v1(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = self.pop_spans()

        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_operation_name_v0_schema(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = self.pop_spans()

        assert spans[0].name == "mysql.query"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_operation_name_v1_schema(self):
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        rows = cursor.fetchall()
        assert len(rows) == 1
        spans = self.pop_spans()

        assert spans[0].name == "mysql.query"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    def test_mysql_dbm_propagation_enabled(self):
        conn = self._get_conn()
        cursor = conn.cursor()

        shared_tests._test_dbm_propagation_enabled(self.tracer, cursor, "mysql")

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    def test_mysql_dbm_propagation_comment_with_global_service_name_configured(self):
        """tests if dbm comment is set in mysql"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.__wrapped__ = mock.Mock()

        shared_tests._test_dbm_propagation_comment_with_global_service_name_configured(
            config=MYSQL_CONFIG, db_system="mysql", cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_AIOMYSQL_SERVICE="service-name-override",
        )
    )
    def test_mysql_dbm_propagation_comment_integration_service_name_override(self):
        """tests if dbm comment is set in mysql"""
        conn = self._get_conn()
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
            DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED="True",
        )
    )
    def test_mysql_dbm_propagation_comment_peer_service_enabled(self):
        """tests if dbm comment is set in mysql"""
        conn = self._get_conn()
        cursor = conn.cursor()
        cursor.__wrapped__ = mock.Mock()

        shared_tests._test_dbm_propagation_comment_peer_service_enabled(
            config=MYSQL_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
        )
