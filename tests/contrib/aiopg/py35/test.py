# 3p
import aiopg

# project
from ddtrace.contrib.aiopg.patch import patch, unpatch
from ddtrace import Pin

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.contrib.asyncio.utils import AsyncioTestCase, mark_asyncio
from ..test import ConnCtx


TEST_PORT = str(POSTGRES_CONFIG['port'])


class TestPsycopgPatch(AsyncioTestCase):
    # default service
    TEST_SERVICE = 'postgres'

    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        super().tearDown()
        unpatch()

    def _get_conn(self):
        Pin(None, tracer=self.tracer).onto(aiopg)
        return ConnCtx(None)

    @mark_asyncio
    async def test_cursor_ctx_manager(self):
        # ensure cursors work with context managers
        # https://github.com/DataDog/dd-trace-py/issues/228

        async with self._get_conn() as conn:
            async with conn.cursor() as cur:
                t = type(cur)

            async with conn.cursor() as cur:
                assert t == type(cur), '%s != %s' % (t, type(cur))
                await cur.execute(operation='select \'blah\'')
                rows = await cur.fetchall()
                assert len(rows) == 1
                assert rows[0][0] == 'blah'

        spans = self.tracer.writer.pop()
        assert len(spans) == 3
        assert spans[0].name == 'postgres.connect'
        assert spans[1].name == 'postgres.execute'
        assert spans[2].name == 'postgres.fetchall'

    @mark_asyncio
    async def test_pool(self):
        Pin(None, tracer=self.tracer).onto(aiopg)

        async with aiopg.create_pool(**POSTGRES_CONFIG,
                                     minsize=1, maxsize=1) as pool:
            async with pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute('select 1;')

        spans = self.tracer.writer.pop()
        assert len(spans) == 4

        assert spans[0].name == 'postgres.connect'
        assert spans[1].name == 'postgres.pool.acquire'
        assert spans[2].name == 'postgres.execute'
        assert spans[3].name == 'postgres.pool.release'
