# stdlib
import time

# 3p
import psycopg2
from psycopg2 import extensions
from psycopg2 import extras

from unittest import skipIf
from nose.tools import eq_, ok_

# project
from ddtrace.contrib.psycopg import connection_factory
from ddtrace.contrib.psycopg.patch import patch, unpatch
from ddtrace import Pin

# testing
from tests.opentracer.utils import init_tracer
from tests.contrib.config import POSTGRES_CONFIG
from tests.test_tracer import get_dummy_tracer


PSYCOPG_VERSION = tuple(map(int, psycopg2.__version__.split()[0].split('.')))
TEST_PORT = str(POSTGRES_CONFIG['port'])
class PsycopgCore(object):

    # default service
    TEST_SERVICE = 'postgres'

    def _get_conn_and_tracer(self):
        # implement me
        pass

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
        eq_(rows, [('foobarblah',)])
        assert rows
        spans = writer.pop()
        assert spans
        eq_(len(spans), 2)
        span = spans[0]
        eq_(span.name, "postgres.query")
        eq_(span.resource, q)
        eq_(span.service, service)
        ok_(span.get_tag("sql.query") is None)
        eq_(span.error, 0)
        eq_(span.span_type, "sql")
        assert start <= span.start <= end
        assert span.duration <= end - start

        fetch_span = spans[1]
        eq_(fetch_span.name, "postgres.query.fetchall")

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
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.name, "postgres.query")
        eq_(span.resource, q)
        eq_(span.service, service)
        ok_(span.get_tag("sql.query") is None)
        eq_(span.error, 1)
        eq_(span.meta["out.host"], "localhost")
        eq_(span.meta["out.port"], TEST_PORT)
        eq_(span.span_type, "sql")

    def test_opentracing_propagation(self):
        # ensure OpenTracing plays well with our integration
        query = "SELECT 'tracing'"
        db, tracer = self._get_conn_and_tracer()
        ot_tracer = init_tracer('psycopg-svc', tracer)

        with ot_tracer.start_active_span('db.access'):
            cursor = db.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()

        eq_(rows, [('tracing',)])
        spans = tracer.writer.pop()
        eq_(len(spans), 3)
        ot_span, dd_span, fetch_span = spans
        # confirm the parenting
        eq_(ot_span.parent_id, None)
        eq_(dd_span.parent_id, ot_span.span_id)
        # check the OpenTracing span
        eq_(ot_span.name, "db.access")
        eq_(ot_span.service, "psycopg-svc")
        # make sure the Datadog span is unaffected by OpenTracing
        eq_(dd_span.name, "postgres.query")
        eq_(dd_span.resource, query)
        eq_(dd_span.service, 'postgres')
        ok_(dd_span.get_tag("sql.query") is None)
        eq_(dd_span.error, 0)
        eq_(dd_span.span_type, "sql")

        eq_(fetch_span.name, 'postgres.query.fetchall')

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
        assert len(spans) == 2
        span, fetch_span = spans
        eq_(span.name, "postgres.query")
        eq_(fetch_span.name, 'postgres.query.fetchall')

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
        eq_(service_meta, expected)


class TestPsycopgPatch(PsycopgCore):

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
        eq_(len(spans), 1)

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
        eq_(len(spans), 1)


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
