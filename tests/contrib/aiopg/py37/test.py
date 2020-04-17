# project
from ddtrace.contrib.aiopg.patch import patch, unpatch

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.contrib.asyncio.utils import AsyncioTestCase, mark_asyncio
from ..test import ConnCtx, CursorCtx


TEST_PORT = str(POSTGRES_CONFIG['port'])


class AiopgTestCase(AsyncioTestCase):
    # default service
    TEST_SERVICE = 'postgres'

    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        super().tearDown()
        unpatch()

    def _get_conn(self):
        return ConnCtx(self.tracer)

    @mark_asyncio
    async def test_async_generator(self):
        async with self._get_conn() as conn, CursorCtx(conn) as cursor:
            q = 'select \'foobarblah\''
            await cursor.execute(q)
            rows = []
            async for row in cursor:
                rows.append(row)

        assert rows == [('foobarblah',)]
        spans = self.tracer.writer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == 'postgres.execute'
