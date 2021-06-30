# stdlib
import time
from unittest import skipIf

# 3p
import psycopg2
from psycopg2 import extensions
from psycopg2 import extras

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.psycopg import connection_factory
from ddtrace.contrib.psycopg.patch import PSYCOPG2_VERSION
from ddtrace.contrib.psycopg.patch import patch
from ddtrace.contrib.psycopg.patch import unpatch
from tests.contrib.config import POSTGRES_CONFIG
from tests.opentracer.utils import init_tracer
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import snapshot


if PSYCOPG2_VERSION >= (2, 7):
    from psycopg2.sql import Identifier
    from psycopg2.sql import Literal
    from psycopg2.sql import SQL

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
            pin.clone(service=service, tracer=self.tracer).onto(conn)

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

        self.assertEquals(rows, [("foobarblah",)])

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
                    "out.port": TEST_PORT,
                },
            ),
        )
        root = self.get_root_span()
        assert_is_measured(root)
        self.assertIsNone(root.get_tag("sql.query"))
        self.reset()

    def test_opentracing_propagation(self):
        # ensure OpenTracing plays well with our integration
        query = """SELECT 'tracing'"""

        db = self._get_conn()
        ot_tracer = init_tracer("psycopg-svc", self.tracer)

        with ot_tracer.start_active_span("db.access"):
            cursor = db.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

        self.assertEquals(rows, [("tracing",)])

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

            self.assertEquals(rows, [("tracing",)])

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
        from ddtrace import patch_all

        patch_all()
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

    def test_analytics_default(self):
        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        span = spans[0]
        self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_analytics_with_rate(self):
        with self.override_config("psycopg", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            conn = self._get_conn()
            conn.cursor().execute("""select 'blah'""")

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            span = spans[0]
            self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    def test_analytics_without_rate(self):
        with self.override_config("psycopg", dict(analytics_enabled=True)):
            conn = self._get_conn()
            conn.cursor().execute("""select 'blah'""")

            spans = self.get_spans()
            self.assertEqual(len(spans), 1)
            span = spans[0]
            self.assertEqual(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_app_service(self):
        """
        When a user specifies a service for the app
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

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_PSYCOPG_SERVICE="mysvc"))
    def test_user_specified_service(self):
        conn = self._get_conn()
        conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service == "mysvc"

    @skipIf(PSYCOPG2_VERSION < (2, 5), "Connection context managers not defined in <2.5.")
    def test_contextmanager_connection(self):
        service = "fo"
        with self._get_conn(service=service) as conn:
            conn.cursor().execute("""select 'blah'""")
            self.assert_structure(dict(name="postgres.query", service=service))


def test_backwards_compatibilty_v3():
    tracer = DummyTracer()
    factory = connection_factory(tracer, service="my-postgres-db")
    conn = psycopg2.connect(connection_factory=factory, **POSTGRES_CONFIG)
    conn.cursor().execute("""select 'blah'""")


@skipIf(PSYCOPG2_VERSION < (2, 7), "quote_ident not available in psycopg2<2.7")
def test_manual_wrap_extension_quote_ident_standalone():
    from ddtrace import patch_all

    patch_all()
    from psycopg2.extensions import quote_ident

    # NOTE: this will crash if it doesn't work.
    #   TypeError: argument 2 must be a connection or a cursor
    conn = psycopg2.connect(**POSTGRES_CONFIG)
    quote_ident("foo", conn)
