# stdlib
import asynctest
import asyncio
import time
import sys

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


TEST_PORT = str(POSTGRES_CONFIG['port'])
PY_35 = sys.version_info >= (3, 5)


class TestPsycopgPatch(asynctest.TestCase):
    # default service
    TEST_SERVICE = 'postgres'

    def setUp(self):
        self._conn = None
        patch()

    def tearDown(self):
        if self._conn and not self._conn.closed:
            self._conn.close()

        unpatch()

    @asyncio.coroutine
    def _get_conn_and_tracer(self):
        conn = self._conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        tracer = get_dummy_tracer()
        Pin.get_from(conn).clone(tracer=tracer).onto(conn)

        return conn, tracer

    @asyncio.coroutine
    def assert_conn_is_traced(self, tracer, db, service):

        # ensure the trace aiopg client doesn't add non-standard
        # methods
        try:
            yield from db.execute("select 'foobar'")
        except AttributeError:
            pass

        writer = tracer.writer
        # Ensure we can run a query and it's correctly traced
        q = "select 'foobarblah'"
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
        cur = yield from db.cursor()
        try:
            yield from cur.execute(q)
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

    if PY_35:
        async def test_cursor_ctx_manager(self):
            # ensure cursors work with context managers
            # https://github.com/DataDog/dd-trace-py/issues/228

            conn, tracer = await self._get_conn_and_tracer()
            cur = await conn.cursor()
            t = type(cur)

            async with conn.cursor() as cur:
                assert t == type(cur), "%s != %s" % (t, type(cur))
                await cur.execute(query="select 'blah';")
                rows = await cur.fetchall()
                assert len(rows) == 1
                assert rows[0][0] == 'blah'

            spans = tracer.writer.pop()
            assert len(spans) == 1
            span = spans[0]
            eq_(span.name, "postgres.query")
    else:
        @asyncio.coroutine
        def test_cursor_ctx_manager(self):
            # ensure cursors work with context managers
            # https://github.com/DataDog/dd-trace-py/issues/228

            conn, tracer = yield from self._get_conn_and_tracer()
            cur = yield from conn.cursor()
            t = type(cur)

            with (yield from conn.cursor()) as cur:
                assert t == type(cur), "%s != %s" % (t, type(cur))
                yield from cur.execute(query="select 'blah'")
                rows = yield from cur.fetchall()
                assert len(rows) == 1
                assert rows[0][0] == 'blah'

            spans = tracer.writer.pop()
            assert len(spans) == 1
            span = spans[0]
            eq_(span.name, "postgres.query")

    @asyncio.coroutine
    def test_disabled_execute(self):
        conn, tracer = yield from self._get_conn_and_tracer()
        tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        yield from (yield from conn.cursor()).execute(query="select 'blah'")
        yield from (yield from conn.cursor()).execute("select 'blah'")
        assert not tracer.writer.pop()

    @asyncio.coroutine
    def test_manual_wrap_extension_types(self):
        conn, _ = yield from self._get_conn_and_tracer()
        # NOTE: this will crash if it doesn't work.
        #   _ext.register_type(_ext.UUID, conn_or_curs)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_uuid(conn_or_curs=conn)

    @asyncio.coroutine
    def test_connect_factory(self):
        tracer = get_dummy_tracer()

        services = ["db", "another"]
        for service in services:
            conn, _ = yield from self._get_conn_and_tracer()
            Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
            yield from self.assert_conn_is_traced(tracer, conn, service)
            conn.close()

        # ensure we have the service types
        service_meta = tracer.writer.pop_services()
        expected = {
            "db": {"app": "postgres", "app_type": "db"},
            "another": {"app": "postgres", "app_type": "db"},
        }
        eq_(service_meta, expected)

    @asyncio.coroutine
    def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        service = "fo"

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)

        # Test unpatch
        unpatch()

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)


if __name__ == '__main__':
    asynctest.main()
