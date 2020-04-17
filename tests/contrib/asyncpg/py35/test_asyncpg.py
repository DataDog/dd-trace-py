# flake8: noqa
# DEV: Skip linting, we lint with Python 2, we'll get SyntaxErrors from `async`
# stdlib
import time

# 3p
import asyncpg.pool

# project
from ddtrace.contrib.asyncpg.patch import patch, unpatch
from ddtrace import Pin

# testing
from tests.contrib.config import POSTGRES_CONFIG
from tests.test_tracer import get_dummy_tracer
from tests.contrib.asyncio.utils import AsyncioTestCase, mark_asyncio

# Update to asyncpg way
POSTGRES_CONFIG = dict(POSTGRES_CONFIG)  # make copy
POSTGRES_CONFIG["database"] = POSTGRES_CONFIG["dbname"]
del POSTGRES_CONFIG["dbname"]

TEST_PORT = str(POSTGRES_CONFIG["port"])


class TestPsycopgPatch(AsyncioTestCase):
    # default service
    TEST_SERVICE = "postgres"

    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        super().tearDown()
        unpatch()

    def _get_conn_ctx(self, service=None, tracer=None):
        Pin(service, tracer=tracer or self.tracer).onto(asyncpg)

        class _ConnCtx:
            async def __aenter__(self):
                self._conn = await asyncpg.connect(**POSTGRES_CONFIG)
                return self._conn

            async def __aexit__(self, exc_type, exc_val, exc_tb):
                await self._conn.close()

        return _ConnCtx()

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
        q = "select 'foobarblah'"
        start = time.time()
        rows = await db.fetch(q, timeout=5)
        end = time.time()
        assert rows == [("foobarblah",)]
        assert rows
        spans = writer.pop()
        assert spans
        assert len(spans) == 2

        # prepare span
        span = spans[0]
        assert span.name == "postgres.prepare"
        assert span.resource == q
        assert span.service == service
        assert span.error == 0
        assert span.span_type == "sql"
        assert start <= span.start <= end
        assert span.duration <= end - start

        # execute span
        span = spans[1]
        assert span.name == "postgres.bind_execute"
        assert span.resource == q
        assert span.service == service
        assert span.error == 0
        assert span.span_type == "sql"
        assert span.metrics["db.rowcount"] == 1
        assert start <= span.start <= end
        assert span.duration <= end - start

        # run a query with an error and ensure all is well
        q = "select * from some_non_existant_table"
        try:
            await db.fetch(q)
        except Exception:
            pass
        else:
            assert 0, "should have an error"

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "postgres.prepare"
        assert span.resource == q
        assert span.service == service
        assert span.error == 1
        assert span.meta["out.host"] == "127.0.0.1"
        assert span.metrics["out.port"] == int(TEST_PORT)
        assert span.span_type == "sql"

    @mark_asyncio
    async def test_pool_dsn(self):
        Pin(None, tracer=self.tracer).onto(asyncpg)
        dsn = "postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(database)s" % POSTGRES_CONFIG
        async with asyncpg.create_pool(dsn, min_size=1, max_size=1) as pool:
            async with pool.acquire() as conn:
                await conn.execute("select 1;")

    @mark_asyncio
    async def test_copy_from(self):
        # This test is here to ensure we don't break the params
        async with self._get_conn_ctx() as conn:

            async def consumer(input):
                pass

            await conn.execute("""CREATE TABLE IF NOT EXISTS mytable (a int);""")

            try:
                await conn.execute("""INSERT INTO mytable (a) VALUES (100), (200), (300);""")

                await conn.copy_from_query("SELECT * FROM mytable WHERE a > $1", 10, output=consumer, format="csv")
            finally:
                await conn.execute("DROP TABLE IF EXISTS mytable")

    @mark_asyncio
    async def test_pool(self):
        Pin(None, tracer=self.tracer).onto(asyncpg)

        for min_size in [0, 1]:
            async with asyncpg.create_pool(**POSTGRES_CONFIG, min_size=min_size, max_size=1) as pool:
                async with pool.acquire() as conn:
                    await conn.execute("select 1;")

            spans = self.tracer.writer.pop()
            assert len(spans) == 6

            if min_size == 0:
                assert spans[0].name == "postgres.pool.acquire"
                assert spans[1].name == "postgres.connect"
            else:
                assert spans[0].name == "postgres.connect"
                assert spans[1].name == "postgres.pool.acquire"
            assert spans[2].name == "postgres.query"

            # the ordering changes between python3.5 and later
            assert {spans[3].name, spans[4].name, spans[5].name} == \
                   {"postgres.pool.release",  "postgres.query", "postgres.close"}

    @mark_asyncio
    async def test_disabled_execute(self):
        self.tracer.enabled = False
        async with self._get_conn_ctx() as conn:
            # these calls were crashing with a previous version of the code.
            await conn.execute("select 'blah'")
            await conn.execute("select 'blah'")
            assert not self.tracer.writer.pop()

    @mark_asyncio
    async def test_connect_factory(self):
        tracer = get_dummy_tracer()

        services = ["db", "another"]
        for service in services:
            async with self._get_conn_ctx(service, tracer) as conn:
                await self.assert_conn_is_traced(tracer, conn, service)

        # ensure we have the service types
        service_meta = tracer.writer.pop_services()
        expected = {}
        assert service_meta == expected

    @mark_asyncio
    async def test_patch_unpatch(self):
        tracer = get_dummy_tracer()
        writer = tracer.writer

        # Test patch idempotence
        patch()
        patch()

        service = "fo"
        Pin(service, tracer=tracer).onto(asyncpg)

        conn = await asyncpg.connect(**POSTGRES_CONFIG)
        await conn.execute("select 'blah'")
        await conn.close()

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 3

        # Test unpatch
        unpatch()

        conn = await asyncpg.connect(**POSTGRES_CONFIG)
        await conn.execute("select 'blah'")
        await conn.close()

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()
        conn = await asyncpg.connect(**POSTGRES_CONFIG)
        await conn.execute("select 'blah'")
        await conn.close()

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 3
