# stdlib
import time

# 3p
import psycopg2
from psycopg2 import extensions
from psycopg2 import extras

import unittest
from unittest import skipIf

# project
from ddtrace.contrib.psycopg import connection_factory
from ddtrace.contrib.psycopg.patch import patch, unpatch, PSYCOPG_VERSION
from ddtrace import Pin

if PSYCOPG_VERSION >= (2, 7):
    from psycopg2.sql import SQL

# testing
from tests.opentracer.utils import init_tracer
from tests.contrib.config import POSTGRES_CONFIG
from tests.test_tracer import get_dummy_tracer


TEST_PORT = str(POSTGRES_CONFIG['port'])
class PsycopgCore(unittest.TestCase):

    # default service
    TEST_SERVICE = 'postgres'

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()

    def _get_conn_and_tracer(self):
        conn = psycopg2.connect(**POSTGRES_CONFIG)
        tracer = get_dummy_tracer()
        Pin.get_from(conn).clone(tracer=tracer).onto(conn)

        return conn, tracer

    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        service = "fo"

        conn = psycopg2.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
        conn.cursor().execute("select 'blah'")

        spans = writer.pop()
        assert spans, spans
        self.assertEquals(len(spans), 1)

        # Test unpatch
        unpatch()

        conn = psycopg2.connect(**POSTGRES_CONFIG)
        conn.cursor().execute("select 'blah'")

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        conn = psycopg2.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
        conn.cursor().execute("select 'blah'")

        spans = writer.pop()
        assert spans, spans
        self.assertEquals(len(spans), 1)


    def assert_conn_is_traced(self, tracer, db, service):

        # ensure the trace pscyopg client doesn't add non-standard
        # methods
        try:
            db.execute("select 'foobar'")
        except AttributeError:
            pass

        writer = tracer.writer
        # Ensure we can run a query and it's correctly traced
        q = "select 'foobarblah'"
        start = time.time()
        cursor = db.cursor()
        cursor.execute(q)
        rows = cursor.fetchall()
        end = time.time()
        self.assertEquals(rows, [('foobarblah',)])
        assert rows
        spans = writer.pop()
        assert spans
        self.assertEquals(len(spans), 1)
        span = spans[0]
        self.assertEquals(span.name, "postgres.query")
        self.assertEquals(span.resource, q)
        self.assertEquals(span.service, service)
        self.assertTrue(span.get_tag("sql.query") is None)
        self.assertEquals(span.error, 0)
        self.assertEquals(span.span_type, "sql")
        assert start <= span.start <= end
        assert span.duration <= end - start

        # run a query with an error and ensure all is well
        q = "select * from some_non_existant_table"
        cur = db.cursor()
        try:
            cur.execute(q)
        except Exception:
            pass
        else:
            assert 0, "should have an error"
        spans = writer.pop()
        assert spans, spans
        self.assertEquals(len(spans), 1)
        span = spans[0]
        self.assertEquals(span.name, "postgres.query")
        self.assertEquals(span.resource, q)
        self.assertEquals(span.service, service)
        self.assertTrue(span.get_tag("sql.query") is None)
        self.assertEquals(span.error, 1)
        self.assertEquals(span.meta["out.host"], "localhost")
        self.assertEquals(span.meta["out.port"], TEST_PORT)
        self.assertEquals(span.span_type, "sql")

    def test_opentracing_propagation(self):
        # ensure OpenTracing plays well with our integration
        query = "SELECT 'tracing'"
        db, tracer = self._get_conn_and_tracer()
        ot_tracer = init_tracer('psycopg-svc', tracer)

        with ot_tracer.start_active_span('db.access'):
            cursor = db.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

        self.assertEquals(rows, [('tracing',)])
        spans = tracer.writer.pop()
        self.assertEquals(len(spans), 2)
        ot_span, dd_span = spans
        # confirm the parenting
        self.assertEquals(ot_span.parent_id, None)
        self.assertEquals(dd_span.parent_id, ot_span.span_id)
        # check the OpenTracing span
        self.assertEquals(ot_span.name, "db.access")
        self.assertEquals(ot_span.service, "psycopg-svc")
        # make sure the Datadog span is unaffected by OpenTracing
        self.assertEquals(dd_span.name, "postgres.query")
        self.assertEquals(dd_span.resource, query)
        self.assertEquals(dd_span.service, 'postgres')
        self.assertTrue(dd_span.get_tag("sql.query") is None)
        self.assertEquals(dd_span.error, 0)
        self.assertEquals(dd_span.span_type, "sql")

    @skipIf(PSYCOPG_VERSION < (2, 5), 'context manager not available in psycopg2==2.4')
    def test_cursor_ctx_manager(self):
        # ensure cursors work with context managers
        # https://github.com/DataDog/dd-trace-py/issues/228
        conn, tracer = self._get_conn_and_tracer()
        t = type(conn.cursor())
        with conn.cursor() as cur:
            assert t == type(cur), "%s != %s" % (t, type(cur))
            cur.execute(query="select 'blah'")
            rows = cur.fetchall()
            assert len(rows) == 1, row
            assert rows[0][0] == 'blah'

        spans = tracer.writer.pop()
        assert len(spans) == 1
        span = spans[0]
        self.assertEquals(span.name, "postgres.query")

    def test_disabled_execute(self):
        conn, tracer = self._get_conn_and_tracer()
        tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        conn.cursor().execute(query="select 'blah'")
        conn.cursor().execute("select 'blah'")
        assert not tracer.writer.pop()

    @skipIf(PSYCOPG_VERSION < (2, 5), '_json is not available in psycopg2==2.4')
    def test_manual_wrap_extension_types(self):
        conn, _ = self._get_conn_and_tracer()
        # NOTE: this will crash if it doesn't work.
        #   _ext.register_type(_ext.UUID, conn_or_curs)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_uuid(conn_or_curs=conn)

        # NOTE: this will crash if it doesn't work.
        #   _ext.register_default_json(conn)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_default_json(conn)

    def test_manual_wrap_extension_adapt(self):
        conn, _ = self._get_conn_and_tracer()
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
        binary = extensions.adapt(b'12345')
        binary.prepare(conn)

    @skipIf(PSYCOPG_VERSION < (2, 7), 'quote_ident not available in psycopg2<2.7')
    def test_manual_wrap_extension_quote_ident(self):
        from ddtrace import patch_all
        patch_all()
        from psycopg2.extensions import quote_ident

        # NOTE: this will crash if it doesn't work.
        #   TypeError: argument 2 must be a connection or a cursor
        conn = psycopg2.connect(**POSTGRES_CONFIG)
        quote_ident('foo', conn)

    def test_connect_factory(self):
        tracer = get_dummy_tracer()

        services = ["db", "another"]
        for service in services:
            conn, _ = self._get_conn_and_tracer()
            Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
            self.assert_conn_is_traced(tracer, conn, service)

        # ensure we have the service types
        service_meta = tracer.writer.pop_services()
        expected = {
            "db" : {"app":"postgres", "app_type":"db"},
            "another" : {"app":"postgres", "app_type":"db"},
        }
        self.assertEquals(service_meta, expected)


    @skipIf(PSYCOPG_VERSION < (2, 7), 'SQL string composition not available in psycopg2<2.7')
    def test_composed_query(self):
        """ Checks whether execution of composed SQL string is traced """
        query = SQL(' union all ' ).join(
            [SQL("""select 'one' as x"""),
             SQL("""select 'two' as x""")])
        db, tracer = self._get_conn_and_tracer()

        with db.cursor() as cur:
            cur.execute(query=query)
            rows = cur.fetchall()
            assert len(rows) == 2, rows
            assert rows[0][0] == 'one'
            assert rows[1][0] == 'two'

        spans = tracer.writer.pop()
        assert len(spans) == 1
        span = spans[0]
        self.assertEquals(span.name, "postgres.query")


def test_backwards_compatibilty_v3():
    tracer = get_dummy_tracer()
    factory = connection_factory(tracer, service="my-postgres-db")
    conn = psycopg2.connect(connection_factory=factory, **POSTGRES_CONFIG)
    conn.cursor().execute("select 'blah'")


@skipIf(PSYCOPG_VERSION < (2, 7), 'quote_ident not available in psycopg2<2.7')
def test_manual_wrap_extension_quote_ident_standalone():
    from ddtrace import patch_all
    patch_all()
    from psycopg2.extensions import quote_ident

    # NOTE: this will crash if it doesn't work.
    #   TypeError: argument 2 must be a connection or a cursor
    conn = psycopg2.connect(**POSTGRES_CONFIG)
    quote_ident('foo', conn)
