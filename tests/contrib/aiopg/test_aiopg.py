# stdlib
import time
import asyncio

# 3p
import aiopg
from psycopg2 import extras
from nose.tools import eq_

# project
from ddtrace.contrib.aiopg.patch import patch, unpatch
from ddtrace import Pin

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.test_tracer import get_dummy_tracer
from tests.contrib.asyncio.utils import AsyncioTestCase, mark_asyncio


TEST_PORT = str(POSTGRES_CONFIG['port'])


class TestPsycopgPatch(AsyncioTestCase):
    # default service
    TEST_SERVICE = 'postgres'

    def setUp(self):
        super().setUp()
        self._conn = None
        patch()

    def tearDown(self):
        super().tearDown()
        if self._conn and not self._conn.closed:
            self._conn.close()

        unpatch()

    @asyncio.coroutine
    def _get_conn_and_tracer(self):
        conn = self._conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)

        return conn, self.tracer

    @asyncio.coroutine
    def assert_conn_is_traced(self, tracer, db, service):

        # ensure the trace aiopg client doesn't add non-standard
        # methods
        try:
            yield from db.execute('select \'foobar\'')
        except AttributeError:
            pass

        writer = tracer.writer
        # Ensure we can run a query and it's correctly traced
        q = 'select \'foobarblah\''
        start = time.time()
        cursor = yield from db.cursor()
        yield from cursor.execute(q)
        rows = yield from cursor.fetchall()
        end = time.time()
        eq_(rows, [('foobarblah',)])
        assert rows
        spans = writer.pop()
        assert spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.name, 'postgres.query')
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.meta['sql.query'], q)
        eq_(span.error, 0)
        eq_(span.span_type, 'sql')
        assert start <= span.start <= end
        assert span.duration <= end - start

        # run a query with an error and ensure all is well
        q = 'select * from some_non_existant_table'
        cur = yield from db.cursor()
        try:
            yield from cur.execute(q)
        except Exception:
            pass
        else:
            assert 0, 'should have an error'
        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.name, 'postgres.query')
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.meta['sql.query'], q)
        eq_(span.error, 1)
        eq_(span.meta['out.host'], 'localhost')
        eq_(span.meta['out.port'], TEST_PORT)
        eq_(span.span_type, 'sql')

    @mark_asyncio
    def test_disabled_execute(self):
        conn, tracer = yield from self._get_conn_and_tracer()
        tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        yield from (yield from conn.cursor()).execute(query='select \'blah\'')
        yield from (yield from conn.cursor()).execute('select \'blah\'')
        assert not tracer.writer.pop()

    @mark_asyncio
    def test_manual_wrap_extension_types(self):
        conn, _ = yield from self._get_conn_and_tracer()
        # NOTE: this will crash if it doesn't work.
        #   _ext.register_type(_ext.UUID, conn_or_curs)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_uuid(conn_or_curs=conn)

    @mark_asyncio
    def test_connect_factory(self):
        tracer = get_dummy_tracer()

        services = ['db', 'another']
        for service in services:
            conn, _ = yield from self._get_conn_and_tracer()
            Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
            yield from self.assert_conn_is_traced(tracer, conn, service)
            conn.close()

        # ensure we have the service types
        service_meta = tracer.writer.pop_services()
        expected = {
            'db': {'app': 'postgres', 'app_type': 'db'},
            'another': {'app': 'postgres', 'app_type': 'db'},
        }
        eq_(service_meta, expected)

    @mark_asyncio
    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        service = 'fo'

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
        yield from (yield from conn.cursor()).execute('select \'blah\'')
        conn.close()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        yield from (yield from conn.cursor()).execute('select \'blah\'')
        conn.close()

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
        yield from (yield from conn.cursor()).execute('select \'blah\'')
        conn.close()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
