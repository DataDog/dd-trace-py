# stdlib
import time

import mock
import psycopg
from psycopg.sql import SQL
from psycopg.sql import Composed
from psycopg.sql import Identifier
from psycopg.sql import Literal

from ddtrace.contrib.internal.psycopg.patch import patch
from ddtrace.contrib.internal.psycopg.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.internal.utils.version import parse_version
from ddtrace.trace import Pin
from tests.contrib.config import POSTGRES_CONFIG
from tests.opentracer.utils import init_tracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import snapshot


PSYCOPG_VERSION = parse_version(psycopg.__version__)
TEST_PORT = POSTGRES_CONFIG["port"]


class PsycopgCore(TracerTestCase):
    # default service
    TEST_SERVICE = "postgres"

    def setUp(self):
        super(PsycopgCore, self).setUp()

        patch()

    def tearDown(self):
        super(PsycopgCore, self).tearDown()

        unpatch()

    def _get_conn(self, service=None):
        conn = psycopg.connect(**POSTGRES_CONFIG)
        pin = Pin.get_from(conn)
        if pin:
            pin._clone(service=service, tracer=self.tracer).onto(conn)

        return conn

    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        service = "fo"

        conn = self._get_conn(service=service)
        conn.cursor().execute("""select 'blah'""")
        self.assert_structure(dict(name="postgres.query", service=service))
        self.reset()

        # Test unpatch
        unpatch()

        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")
        self.assert_has_no_spans()

        # Test patch again
        patch()

        conn = self._get_conn(service=service)
        conn.cursor().execute("""select 'blah'""")
        self.assert_structure(dict(name="postgres.query", service=service))

    def assert_conn_is_traced(self, db, service):
        # ensure the trace pscyopg client doesn't add non-standard
        # methods
        try:
            db.executemany("select %s", (("str_foo",), ("str_bar",)))
        except AttributeError:
            pass

        # Ensure we can run a query and it's correctly traced
        q = """select 'foobarblah'"""

        start = time.time()
        cursor = db.cursor()
        res = cursor.execute(q)  # execute now returns the cursor
        self.assertEqual(psycopg.Cursor, type(res))
        rows = res.fetchall()
        end = time.time()

        self.assertEqual(rows, [("foobarblah",)])

        self.assert_structure(
            dict(name="postgres.query", resource=q, service=service, error=0, span_type="sql"),
        )
        root = self.get_root_span()
        self.assertIsNone(root.get_tag("sql.query"))
        assert start <= root.start <= end
        assert root.duration <= end - start
        # confirm analytics disabled by default
        self.reset()

        # run a query with an error and ensure all is well
        q = """select * from some_non_existant_table"""
        cur = db.cursor()
        try:
            cur.execute(q)
        except Exception:
            pass
        else:
            assert 0, "should have an error"

        self.assert_structure(
            dict(
                name="postgres.query",
                resource=q,
                service=service,
                error=1,
                span_type="sql",
                meta={
                    "out.host": "127.0.0.1",
                },
                metrics={
                    "network.destination.port": TEST_PORT,
                },
            ),
        )
        root = self.get_root_span()
        assert root.get_tag("component") == "psycopg"
        assert root.get_tag("span.kind") == "client"
        assert_is_measured(root)
        self.assertIsNone(root.get_tag("sql.query"))
        self.reset()

    def test_psycopg3_connection_with_string(self):
        # Regression test for DataDog/dd-trace-py/issues/5926
        configs_arr = ["{}={}".format(k, v) for k, v in POSTGRES_CONFIG.items()]
        configs_arr.append("options='-c statement_timeout=1000 -c lock_timeout=250'")
        conn = psycopg.connect(" ".join(configs_arr))

        Pin.get_from(conn)._clone(service="postgres", tracer=self.tracer).onto(conn)
        self.assert_conn_is_traced(conn, "postgres")

    def test_opentracing_propagation(self):
        # ensure OpenTracing plays well with our integration
        query = """SELECT 'tracing'"""

        db = self._get_conn()
        ot_tracer = init_tracer("psycopg-svc", self.tracer)

        with ot_tracer.start_active_span("db.access"):
            cursor = db.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

        self.assertEqual(rows, [("tracing",)])

        self.assert_structure(
            dict(name="db.access", service="psycopg-svc"),
            (dict(name="postgres.query", resource=query, service="postgres", error=0, span_type="sql"),),
        )
        assert_is_measured(self.get_spans()[1])
        self.reset()

        with self.override_config("psycopg", dict(trace_fetch_methods=True)):
            db = self._get_conn()
            ot_tracer = init_tracer("psycopg-svc", self.tracer)

            with ot_tracer.start_active_span("db.access"):
                cursor = db.cursor()
                cursor.execute(query)
                rows = cursor.fetchall()

            self.assertEqual(rows, [("tracing",)])

            self.assert_structure(
                dict(name="db.access", service="psycopg-svc"),
                (
                    dict(name="postgres.query", resource=query, service="postgres", error=0, span_type="sql"),
                    dict(name="postgres.query.fetchall", resource=query, service="postgres", error=0, span_type="sql"),
                ),
            )
            assert_is_measured(self.get_spans()[1])

    def test_cursor_ctx_manager(self):
        # ensure cursors work with context managers
        # https://github.com/DataDog/dd-trace-py/issues/228
        conn = self._get_conn()
        t = type(conn.cursor())
        with conn.cursor() as cur:
            assert t == type(cur), "{} != {}".format(t, type(cur))
            cur.execute(query="""select 'blah'""")
            rows = cur.fetchall()
            assert len(rows) == 1, rows
            assert rows[0][0] == "blah"

        assert_is_measured(self.get_root_span())
        self.assert_structure(
            dict(name="postgres.query"),
        )

    def test_disabled_execute(self):
        conn = self._get_conn()
        self.tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        conn.cursor().execute(query="""select 'blah'""")
        conn.cursor().execute("""select 'blah'""")
        self.assert_has_no_spans()

    def test_connect_factory(self):
        services = ["db", "another"]
        for service in services:
            conn = self._get_conn(service=service)
            self.assert_conn_is_traced(conn, service)

    def test_commit(self):
        conn = self._get_conn()
        conn.commit()

        self.assert_structure(dict(name="psycopg.connection.commit", service=self.TEST_SERVICE))

    def test_rollback(self):
        conn = self._get_conn()
        conn.rollback()

        self.assert_structure(dict(name="psycopg.connection.rollback", service=self.TEST_SERVICE))

    def test_composed_query(self):
        """Checks whether execution of composed SQL string is traced"""
        query = SQL(" union all ").join(
            [SQL("""select {} as x""").format(Literal("one")), SQL("""select {} as x""").format(Literal("two"))]
        )
        db = self._get_conn()

        with db.cursor() as cur:
            cur.execute(query=query)
            rows = cur.fetchall()
            assert len(rows) == 2, rows
            assert rows[0][0] == "one"
            assert rows[1][0] == "two"

        assert_is_measured(self.get_root_span())
        self.assert_structure(
            dict(name="postgres.query", resource=query.as_string(db)),
        )

    def test_composed_query_identifier(self):
        """Checks whether execution of composed SQL string is traced"""
        db = self._get_conn()
        with db.cursor() as cur:
            # DEV: Use a temp table so it is removed after this session
            cur.execute("CREATE TEMP TABLE test (id serial PRIMARY KEY, name varchar(12) NOT NULL UNIQUE);")
            cur.execute("INSERT INTO test (name) VALUES (%s);", ("test_case",))
            spans = self.get_spans()
            assert len(spans) == 2
            self.reset()

            query = SQL("""select {}, {} from {}""").format(Identifier("id"), Identifier("name"), Identifier("test"))
            cur.execute(query=query)
            rows = cur.fetchall()
            assert rows == [(1, "test_case")]

            assert_is_measured(self.get_root_span())
            self.assert_structure(
                dict(name="postgres.query", resource=query.as_string(db)),
            )

    @snapshot()
    def test_composed_query_encoding(self):
        """Checks whether execution of composed SQL string is traced"""
        import logging

        logger = logging.getLogger()
        logger.level = logging.DEBUG
        query = SQL(" union all ").join([SQL("""select 'one' as x"""), SQL("""select 'two' as x""")])
        conn = psycopg.connect(**POSTGRES_CONFIG)

        with conn.cursor() as cur:
            cur.execute(query=query)
            rows = cur.fetchall()
            assert len(rows) == 2, rows
            assert rows[0][0] == "one"
            assert rows[1][0] == "two"

    def test_connection_execute(self):
        """Checks whether connection execute shortcute method works as normal"""

        query = SQL("""select 'one' as x""")
        cur = self._get_conn().execute(query)

        rows = cur.fetchall()
        assert len(rows) == 1, rows
        assert rows[0][0] == "one"

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)

        query_span = spans[0]
        assert query_span.name == "postgres.query"

    def test_cursor_from_connection_shortcut(self):
        """Checks whether connection execute shortcute method works as normal"""

        query = SQL("""select 'one' as x""")
        conn = self._get_conn()

        cur = psycopg.Cursor(connection=conn)
        cur.execute(query)

        rows = cur.fetchall()
        assert len(rows) == 1, rows
        assert rows[0][0] == "one"

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)

        query_span = spans[0]
        assert query_span.name == "postgres.query"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_app_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The psycopg integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service != "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_app_service_v1(self):
        """
        v0: When a user specifies a service for the app
            The psycopg integration should use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_PSYCOPG_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_user_specified_service_v0(self):
        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_PSYCOPG_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    def test_user_specified_service_v1(self):
        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service == "mysvc"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_unspecified_service_v0(self):
        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service == "postgres"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1(self):
        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_span_name_v0_schema(self):
        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].name == "postgres.query"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_span_name_v1_schema(self):
        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].name == "postgresql.query"

    def test_contextmanager_connection(self):
        service = "fo"
        with self._get_conn(service=service) as conn:
            conn.cursor().execute("""select 'blah'""")
            self.assert_structure(dict(name="postgres.query", service=service))

    @snapshot()
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    def test_postgres_dbm_propagation_tag(self):
        """generates snapshot to check whether execution of SQL string sets dbm propagation tag"""
        conn = psycopg.connect(**POSTGRES_CONFIG)
        cursor = conn.cursor()
        # test string queries
        cursor.execute("select 'str blah'")
        cursor.executemany("select %s", (("str_foo",), ("str_bar",)))
        # test byte string queries
        cursor.execute(b"select 'byte str blah'")
        cursor.executemany(b"select %s", ((b"bstr_foo",), (b"bstr_bar",)))
        # test composed queries
        cursor.execute(SQL("select 'composed_blah'"))
        cursor.executemany(SQL("select %s"), (("composed_foo",), ("composed_bar",)))

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    def test_postgres_dbm_propagation_comment(self):
        """tests if dbm comment is set in postgres"""
        conn = psycopg.connect(**POSTGRES_CONFIG)
        cursor = conn.cursor()
        cursor.__wrapped__ = mock.Mock()
        # test string queries
        cursor.execute("select 'blah'")
        cursor.executemany("select %s", (("foo",), ("bar",)))
        dbm_comment = (
            "/*dddb='postgres',dddbs='postgres',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
            "ddpv='v7343437-d7ac743'*/ "
        )
        cursor.__wrapped__.execute.assert_called_once_with(dbm_comment + "select 'blah'")
        cursor.__wrapped__.executemany.assert_called_once_with(dbm_comment + "select %s", (("foo",), ("bar",)))
        # test byte string queries
        cursor.__wrapped__.reset_mock()
        cursor.execute(b"select 'blah'")
        cursor.executemany(b"select %s", ((b"foo",), (b"bar",)))
        cursor.__wrapped__.execute.assert_called_once_with(dbm_comment.encode() + b"select 'blah'")
        cursor.__wrapped__.executemany.assert_called_once_with(
            dbm_comment.encode() + b"select %s", ((b"foo",), (b"bar",))
        )
        # test composed queries
        cursor.__wrapped__.reset_mock()
        cursor.execute(SQL("select 'blah'"))
        cursor.executemany(SQL("select %s"), (("foo",), ("bar",)))
        cursor.__wrapped__.execute.assert_called_once_with(
            Composed(
                [
                    SQL(dbm_comment),
                    SQL("select 'blah'"),
                ]
            )
        )
        cursor.__wrapped__.executemany.assert_called_once_with(
            Composed(
                [
                    SQL(dbm_comment),
                    SQL("select %s"),
                ]
            ),
            (("foo",), ("bar",)),
        )

    def test_patch_and_unpatch_several_times(self):
        """Patches and unpatches module sequentially to ensure proper functionality"""

        def execute_query_and_get_spans(n_spans):
            query = SQL("""select 'one' as x""")
            cur = self._get_conn().execute(query)

            rows = cur.fetchall()
            assert len(rows) == 1, rows
            spans = self.pop_spans()
            self.assertEqual(len(spans), n_spans)

        execute_query_and_get_spans(1)
        unpatch()
        execute_query_and_get_spans(0)
        patch()
        execute_query_and_get_spans(1)
        unpatch()
        execute_query_and_get_spans(0)
        patch()
        patch()
        execute_query_and_get_spans(1)
        unpatch()
        unpatch()
        execute_query_and_get_spans(0)

    def test_connection_instance_method_patch(self):
        """Checks whether connection instance method connect works as intended"""

        other_conn = self._get_conn()
        conn = psycopg.Connection(other_conn.pgconn)
        connection = conn.connect(**POSTGRES_CONFIG)

        pin = Pin.get_from(connection)
        if pin:
            pin._clone(service="postgres", tracer=self.tracer).onto(connection)

        query = SQL("""select 'one' as x""")
        cur = connection.execute(query)

        rows = cur.fetchall()
        assert len(rows) == 1, rows
        assert rows[0][0] == "one"

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)

        query_span = spans[0]
        assert query_span.name == "postgres.query"
