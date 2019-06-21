# stdlib
import asyncio

# 3p
import aiopg

# project
from ddtrace.contrib.aiopg.patch import patch, unpatch
from ddtrace import Pin

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.contrib.asyncio.utils import AsyncioTestCase, mark_asyncio, mark_sync


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

    async def _get_conn_and_tracer(self):
        Pin(None, tracer=self.tracer).onto(aiopg)
        conn = self._conn = await aiopg.connect(**POSTGRES_CONFIG)
        return conn, self.tracer

    async def _test_cursor_ctx_manager(self):
        conn, tracer = await self._get_conn_and_tracer()
        cur = await conn.cursor()
        t = type(cur)

        async with conn.cursor() as cur:
            assert t == type(cur), '%s != %s' % (t, type(cur))
            await cur.execute(operation='select \'blah\'')
            rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == 'blah'

        spans = tracer.writer.pop()
        assert len(spans) == 3
        assert spans[0].name == 'postgres.connect'
        assert spans[1].name == 'postgres.execute'
        assert spans[2].name == 'postgres.fetchall'

    @mark_asyncio
    def test_cursor_ctx_manager(self):
        # ensure cursors work with context managers
        # https://github.com/DataDog/dd-trace-py/issues/228
        yield from self._test_cursor_ctx_manager()

    @mark_sync
    async def test_pool(self):
        Pin(None, tracer=self.tracer).onto(aiopg)

        async with aiopg.create_pool(**POSTGRES_CONFIG,
                                     minsize=1, maxsize=1) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('select 1;')

        spans = self.tracer.writer.pop()
        eq_(len(spans), 4)

        eq_(spans[0].name, "postgres.connect")
        eq_(spans[1].name, "postgres.pool.acquire")
        eq_(spans[2].name, "postgres.execute")
        eq_(spans[3].name, "postgres.pool.release")
