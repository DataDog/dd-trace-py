# stdlib
import time
from unittest import skipIf

import mock
import psycopg2
from psycopg2 import extensions
from psycopg2 import extras

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


PSYCOPG2_VERSION = parse_version(psycopg2.__version__)


if PSYCOPG2_VERSION >= (2, 7):
    from psycopg2.sql import SQL
    from psycopg2.sql import Composed
    from psycopg2.sql import Identifier
    from psycopg2.sql import Literal

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
        conn = psycopg2.connect(**POSTGRES_CONFIG)
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
            db.execute("""select 'foobar'""")
        except AttributeError:
            pass

        # Ensure we can run a query and it's correctly traced
        q = """select 'foobarblah'"""

        start = time.time()
        cursor = db.cursor()
        res = cursor.execute(q)
        self.assertIsNone(res)
        rows = cursor.fetchall()
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
        assert_is_measured(root)
        self.assertIsNone(root.get_tag("sql.query"))
        self.reset()

    def test_psycopg2_connection_with_string(self):
        # Regression test for DataDog/dd-trace-py/issues/5926
        configs_arr = ["{}={}".format(k, v) for k, v in POSTGRES_CONFIG.items()]
        configs_arr.append("options='-c statement_timeout=1000 -c lock_timeout=250'")
        conn = psycopg2.connect(" ".join(configs_arr))

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

    @skipIf(PSYCOPG2_VERSION < (2, 5), "context manager not available in psycopg2==2.4")
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

    @skipIf(PSYCOPG2_VERSION < (2, 5), "_json is not available in psycopg2==2.4")
    def test_manual_wrap_extension_types(self):
        conn = self._get_conn()
        # NOTE: this will crash if it doesn't work.
        #   _ext.register_type(_ext.UUID, conn_or_curs)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_uuid(conn_or_curs=conn)

        # NOTE: this will crash if it doesn't work.
        #   _ext.register_default_json(conn)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_default_json(conn)

    def test_manual_wrap_extension_adapt(self):
        conn = self._get_conn()
        # NOTE: this will crash if it doesn't work.
        #   items = _ext.adapt([1, 2, 3])
        #   items.prepare(conn)
        #   TypeError: argument 2 must be a connection, cursor or None
        items = extensions.adapt([1, 2, 3])
        items.prepare(conn)

        # NOTE: this will crash if it doesn't work.
        #   binary = _ext.adapt(b'12345)
        #   binary.prepare(conn)
        #   TypeError: argument 2 must be a connection, cursor or None
        binary = extensions.adapt(b"12345")
        binary.prepare(conn)

    @skipIf(PSYCOPG2_VERSION < (2, 7), "quote_ident not available in psycopg2<2.7")
    def test_manual_wrap_extension_quote_ident(self):
        from ddtrace._monkey import _patch_all

        _patch_all()
        from psycopg2.extensions import quote_ident

        # NOTE: this will crash if it doesn't work.
        #   TypeError: argument 2 must be a connection or a cursor
        conn = psycopg2.connect(**POSTGRES_CONFIG)
        quote_ident("foo", conn)

    def test_connect_factory(self):
        services = ["db", "another"]
        for service in services:
            conn = self._get_conn(service=service)
            self.assert_conn_is_traced(conn, service)

    def test_commit(self):
        conn = self._get_conn()
        conn.commit()

        self.assert_structure(dict(name="postgres.connection.commit", service=self.TEST_SERVICE))

    def test_rollback(self):
        conn = self._get_conn()
        conn.rollback()

        self.assert_structure(dict(name="postgres.connection.rollback", service=self.TEST_SERVICE))

    @skipIf(PSYCOPG2_VERSION < (2, 7), "SQL string composition not available in psycopg2<2.7")
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

    @skipIf(PSYCOPG2_VERSION < (2, 7), "SQL string composition not available in psycopg2<2.7")
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
    @skipIf(PSYCOPG2_VERSION < (2, 7), "SQL string composition not available in psycopg2<2.7")
    def test_composed_query_encoding(self):
        """Checks whether execution of composed SQL string is traced"""
        import logging

        logger = logging.getLogger()
        logger.level = logging.DEBUG
        query = SQL(" union all ").join([SQL("""select 'one' as x"""), SQL("""select 'two' as x""")])
        conn = psycopg2.connect(**POSTGRES_CONFIG)

        with conn.cursor() as cur:
            cur.execute(query=query)
            rows = cur.fetchall()
            assert len(rows) == 2, rows
            assert rows[0][0] == "one"
            assert rows[1][0] == "two"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_app_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The psycopg2 integration should not use it.
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
        v1: When a user specifies a service for the app
            The psycopg2 integration should use it.
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

    @skipIf(PSYCOPG2_VERSION < (2, 5), "Connection context managers not defined in <2.5.")
    def test_contextmanager_connection(self):
        service = "fo"
        with self._get_conn(service=service) as conn:
            conn.cursor().execute("""select 'blah'""")
            self.assert_structure(dict(name="postgres.query", service=service))

    @snapshot()
    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    def test_postgres_dbm_propagation_tag(self):
        """generates snapshot to check whether execution of SQL string sets dbm propagation tag"""
        conn = psycopg2.connect(**POSTGRES_CONFIG)
        cursor = conn.cursor()
        # test string queries
        cursor.execute("select 'str blah'")
        cursor.executemany("select %s", (("str_foo",), ("str_bar",)))
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
        conn = psycopg2.connect(**POSTGRES_CONFIG)
        cursor = conn.cursor()
        cursor.__wrapped__ = mock.Mock()
        # test string queries
        cursor.execute("select 'blah'")
        cursor.executemany("select %s", (("foo",), ("bar",)))
        dbm_comment = (
            "/*dddb='postgres',dddbs='postgres',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
            "ddpv='v7343437-d7ac743'*/ "
        )
        # test string queries
        cursor.__wrapped__.execute.assert_called_once_with(dbm_comment + "select 'blah'")
        cursor.__wrapped__.executemany.assert_called_once_with(dbm_comment + "select %s", (("foo",), ("bar",)))
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


@skipIf(PSYCOPG2_VERSION < (2, 7), "quote_ident not available in psycopg2<2.7")
def test_manual_wrap_extension_quote_ident_standalone():
    from ddtrace._monkey import _patch_all

    _patch_all()
    from psycopg2.extensions import quote_ident

    # NOTE: this will crash if it doesn't work.
    #   TypeError: argument 2 must be a connection or a cursor
    conn = psycopg2.connect(**POSTGRES_CONFIG)
    quote_ident("foo", conn)
