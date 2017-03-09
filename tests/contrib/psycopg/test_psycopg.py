# stdlib
import time

# 3p
import psycopg2
from psycopg2 import extras
from nose.tools import eq_

# project
from ddtrace.contrib.psycopg import connection_factory
from ddtrace.contrib.psycopg.patch import patch, unpatch
from ddtrace import Pin

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.test_tracer import get_dummy_tracer


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
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.name, "postgres.query")
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.meta["sql.query"], q)
        eq_(span.error, 0)
        eq_(span.span_type, "sql")
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
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.name, "postgres.query")
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.meta["sql.query"], q)
        eq_(span.error, 1)
        eq_(span.meta["out.host"], "localhost")
        eq_(span.meta["out.port"], TEST_PORT)
        eq_(span.span_type, "sql")

    def test_disabled_execute(self):
        conn, tracer = self._get_conn_and_tracer()
        tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        conn.cursor().execute(query="select 'blah'")
        conn.cursor().execute("select 'blah'")
        assert not tracer.writer.pop()

    def test_manual_wrap_extension_types(self):
        conn, _ = self._get_conn_and_tracer()
        # NOTE: this will crash if it doesn't work.
        #   _ext.register_type(_ext.UUID, conn_or_curs)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_uuid(conn_or_curs=conn)

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

