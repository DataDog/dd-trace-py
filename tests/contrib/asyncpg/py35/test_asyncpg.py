# flake8: noqa
# DEV: Skip linting, we lint with Python 2, we'll get SyntaxErrors from `async`
# stdlib
import time

# 3p
import asyncpg.pool
from nose.tools import eq_

# project
from ddtrace.contrib.asyncpg.patch import patch, unpatch
from ddtrace import Pin

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.test_tracer import get_dummy_tracer
from tests.contrib.asyncio.utils import AsyncioTestCase, mark_sync

# Update to asyncpg way
POSTGRES_CONFIG = dict(POSTGRES_CONFIG)  # make copy
POSTGRES_CONFIG['database'] = POSTGRES_CONFIG['dbname']
del POSTGRES_CONFIG['dbname']

TEST_PORT = str(POSTGRES_CONFIG['port'])


class TestPsycopgPatch(AsyncioTestCase):
    # default service
    TEST_SERVICE = 'postgres'

    def setUp(self):
        super().setUp()
        self._conn = None
        patch()

    def tearDown(self):
        if self._conn and not self._conn.is_closed():
            self.loop.run_until_complete(self._conn.close())

        super().tearDown()
        unpatch()

    async def _get_conn_and_tracer(self, service=None, tracer=None):
        Pin(service, tracer=tracer or self.tracer).onto(asyncpg)
        conn = self._conn = await asyncpg.connect(**POSTGRES_CONFIG)
        return conn, self.tracer

    async def assert_conn_is_traced(self, tracer, db, service):

        # ensure the trace aiopg client doesn't add non-standard
        # methods
        try:
            async with db.transaction():
                cursor = await db.cursor("select 'foobar'")
                await cursor.fetch(1)
        except AttributeError:
            pass

        writer = tracer.writer
        writer.pop()

        # Ensure we can run a query and it's correctly traced
        q = 'select \'foobarblah\''
        start = time.time()
        rows = await db.fetch(q)
        end = time.time()
        eq_(rows, [('foobarblah',)])
        assert rows
        spans = writer.pop()
        assert spans
        eq_(len(spans), 2)

        # prepare span
        span = spans[0]
        eq_(span.name, 'postgres.prepare')
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.error, 0)
        eq_(span.span_type, 'sql')
        assert start <= span.start <= end
        assert span.duration <= end - start

        # execute span
        span = spans[1]
        eq_(span.name, 'postgres.bind_execute')
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.error, 0)
        eq_(span.span_type, 'sql')
        eq_(span.metrics['db.rowcount'], 1)
        assert start <= span.start <= end
        assert span.duration <= end - start

        # run a query with an error and ensure all is well
        q = 'select * from some_non_existant_table'
        try:
            await db.fetch(q)
        except Exception:
            pass
        else:
            assert 0, 'should have an error'

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 1)
        span = spans[0]
        eq_(span.name, 'postgres.prepare')
        eq_(span.resource, q)
        eq_(span.service, service)
        eq_(span.error, 1)
        eq_(span.meta['out.host'], 'localhost')
        eq_(span.meta['out.port'], TEST_PORT)
        eq_(span.span_type, 'sql')

    @mark_sync
    async def test_copy_from(self):
        # This test is here to ensure we don't break the params
        Pin(None, tracer=self.tracer).onto(asyncpg)
        conn, tracer = await self._get_conn_and_tracer()

        async def consumer(input):
            pass

        await conn.execute('''CREATE TABLE IF NOT EXISTS mytable (a int);''')

        try:
            await conn.execute(
                '''INSERT INTO mytable (a) VALUES (100), (200), (300);''')

            await conn.copy_from_query(
                'SELECT * FROM mytable WHERE a > $1', 10, output=consumer,
                format='csv')
        finally:
            await conn.execute('DROP TABLE IF EXISTS mytable')

    @mark_sync
    async def test_pool(self):
        Pin(None, tracer=self.tracer).onto(asyncpg)

        for min_size in [0, 1]:
            async with asyncpg.create_pool(**POSTGRES_CONFIG,
                                           min_size=min_size, max_size=1) as pool:
                async with pool.acquire() as conn:
                    await conn.execute('select 1;')

            spans = self.tracer.writer.pop()
            eq_(len(spans), 6)

            if min_size == 0:
                eq_(spans[0].name, "postgres.pool.acquire")
                eq_(spans[1].name, "postgres.connect")
            else:
                eq_(spans[0].name, "postgres.connect")
                eq_(spans[1].name, "postgres.pool.acquire")
            eq_(spans[2].name, "postgres.query")
            eq_(spans[3].name, "postgres.query")
            eq_(spans[4].name, "postgres.pool.release")
            eq_(spans[5].name, "postgres.close")

    @mark_sync
    async def test_disabled_execute(self):
        self.tracer.enabled = False
        conn, tracer = await self._get_conn_and_tracer()
        # these calls were crashing with a previous version of the code.
        await conn.execute('select \'blah\'')
        await conn.execute('select \'blah\'')
        assert not tracer.writer.pop()

    @mark_sync
    async def test_connect_factory(self):
        tracer = get_dummy_tracer()

        services = ['db', 'another']
        for service in services:
            conn, _ = await self._get_conn_and_tracer(service, tracer)
            await self.assert_conn_is_traced(tracer, conn, service)
            await conn.close()

        # ensure we have the service types
        service_meta = tracer.writer.pop_services()
        expected = {
            'db': {'app': 'postgres', 'app_type': 'db'},
            'another': {'app': 'postgres', 'app_type': 'db'},
        }
        eq_(service_meta, expected)

    @mark_sync
    async def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        service = 'fo'
        Pin(service, tracer=tracer).onto(asyncpg)

        conn = await asyncpg.connect(**POSTGRES_CONFIG)
        await conn.execute('select \'blah\'')
        await conn.close()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 3)

        # Test unpatch
        unpatch()

        conn = await asyncpg.connect(**POSTGRES_CONFIG)
        await conn.execute('select \'blah\'')
        await conn.close()

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()
        conn = await asyncpg.connect(**POSTGRES_CONFIG)
        await conn.execute('select \'blah\'')
        await conn.close()

        spans = writer.pop()
        assert spans, spans
        eq_(len(spans), 3)
