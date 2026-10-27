import asyncio
import time
from unittest import mock

import aiopg
from psycopg2 import extras
import pytest

# project
from ddtrace._trace.pin import Pin
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib.internal.aiopg.connection import AIOTracedCursor
from ddtrace.contrib.internal.aiopg.patch import patch
from ddtrace.contrib.internal.aiopg.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.schema.default import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.internal.utils.version import parse_version
from tests.contrib import shared_tests_async
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
from tests.contrib.config import POSTGRES_CONFIG
from tests.subprocesstest import run_in_subprocess
from tests.utils import assert_is_measured


TEST_PORT = POSTGRES_CONFIG["port"]


class AiopgTestCase(AsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._conn = None
        patch()

    def tearDown(self):
        if self._conn and not self._conn.closed:
            self._conn.close()
            if not self.loop.is_closed():
                # Let aiopg's notification queue finish its connection-close callback.
                self.loop.run_until_complete(asyncio.sleep(0))
        super().tearDown()
        unpatch()

    @mark_asyncio
    async def test_query_is_blocked_before_execution(self):
        cursor = mock.AsyncMock()
        traced_cursor = AIOTracedCursor(cursor, Pin())

        for method in ("execute", "executemany"):
            with mock.patch.object(core, "dispatch_event", side_effect=BlockingException) as dispatch_event:
                with pytest.raises(BlockingException):
                    await getattr(traced_cursor, method)("SELECT 1")

            dispatch_event.assert_called_once_with(
                DbQueryEvent(
                    query="SELECT 1", span_name_prefix="postgres", get_encoding=traced_cursor._dbapi_query_encoding
                )
            )
            getattr(cursor, method).assert_not_awaited()

    async def _get_conn(self):
        self._conn = await aiopg.connect(**POSTGRES_CONFIG)
        return self._conn

    async def assert_conn_is_traced(self, tracer, db):
        # ensure the trace aiopg client doesn't add non-standard
        # methods
        try:
            await db.execute("select 'foobar'")
        except AttributeError:
            pass

        # Ensure we can run a query and it's correctly traced
        q = "select 'foobarblah'"
        start = time.time()
        cursor = await db.cursor()
        await cursor.execute(q)
        rows = await cursor.fetchall()
        end = time.time()
        assert rows == [("foobarblah",)]
        assert rows
        spans = self.pop_spans()
        assert spans
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.name == "postgres.query"
        assert span.resource == q
        assert span.service == "postgres"
        assert span.error == 0
        assert span.span_type == "sql"
        assert start <= span.start <= end
        assert span.duration <= end - start
        assert span.get_tag("component") == "aiopg"
        assert span.get_tag("span.kind") == "client"

        # run a query with an error and ensure all is well
        q = "select * from some_non_existant_table"
        cur = await db.cursor()
        try:
            await cur.execute(q)
        except Exception:
            pass
        else:
            assert 0, "should have an error"
        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "postgres.query"
        assert span.resource == q
        assert span.service == "postgres"
        assert span.error == 1
        assert span.get_metric("network.destination.port") == TEST_PORT
        assert span.span_type == "sql"
        assert span.get_tag("component") == "aiopg"
        assert span.get_tag("span.kind") == "client"

    @mark_asyncio
    async def test_async_generator(self):
        conn = await self._get_conn()
        cursor = await conn.cursor()
        q = "select 'foobarblah'"
        await cursor.execute(q)
        rows = []
        async for row in cursor:
            rows.append(row)

        assert rows == [("foobarblah",)]
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "postgres.query"

    @mark_asyncio
    async def test_disabled_execute(self):
        conn = await self._get_conn()
        self.tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        await (await conn.cursor()).execute(query="select 'blah'")
        await (await conn.cursor()).execute("select 'blah'")
        assert not self.pop_spans()

    @mark_asyncio
    async def test_manual_wrap_extension_types(self):
        conn = await self._get_conn()
        # NOTE: this will crash if it doesn't work.
        #   _ext.register_type(_ext.UUID, conn_or_curs)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_uuid(conn_or_curs=conn)

    @mark_asyncio
    async def test_connect_factory(self):
        conn = await self._get_conn()
        await self.assert_conn_is_traced(self.tracer, conn)
        conn.close()

    @mark_asyncio
    async def test_byte_query_uses_connection_encoding(self):
        for encoding, codec in (("UTF8", "utf-8"), ("WIN1252", "cp1252")):
            async with aiopg.connect(**POSTGRES_CONFIG, options=f"-c client_encoding={encoding}") as connection:
                async with connection.cursor() as cursor:
                    await shared_tests_async._test_byte_query_event(cursor, codec, "postgres")

    @mark_asyncio
    async def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        conn = await self._get_conn()
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        conn = await self._get_conn()
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert not spans, spans

        # Test patch again
        patch()

        conn = await self._get_conn()
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

    @mark_asyncio
    async def test_connection_ctx_manager(self):
        for connect in (aiopg.connect, aiopg.connection.connect, aiopg.pool.connect):
            async with connect(**POSTGRES_CONFIG) as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")
                    assert await cursor.fetchone() == (1,)
                assert cursor.closed
            assert conn.closed
            assert len(self.pop_spans()) == 1

    @mark_asyncio
    async def test_awaited_context_managers(self):
        conn = await self._get_conn()
        with pytest.raises(ValueError, match="close connection"):
            async with conn as entered_conn:
                assert entered_conn is conn
                cursor = await conn.cursor()
                async with cursor as entered_cursor:
                    assert entered_cursor is cursor
                    await entered_cursor.execute("SELECT 1")
                    raise ValueError("close connection")
        assert cursor.closed
        assert conn.closed
        assert len(self.pop_spans()) == 1

    @mark_asyncio
    async def test_connection_context_close(self):
        closed = []
        for connect in (aiopg.connect.__wrapped__, aiopg.connect):
            context = connect(**POSTGRES_CONFIG)
            conn = await context
            try:
                context.close()
                closed.append(conn.closed)
            finally:
                conn.close()
        assert closed[0] == closed[1]

    @mark_asyncio
    async def test_connection_context_abandoned(self):
        connections = []
        original_init = aiopg.connection.Connection.__init__

        def initialize(conn, *args, **kwargs):
            original_init(conn, *args, **kwargs)
            connections.append(conn)

        with mock.patch.object(aiopg.connection.Connection, "__init__", initialize):
            try:
                context = aiopg.connect(**POSTGRES_CONFIG)
                context.close()
                assert all(conn.closed for conn in connections)
                task = asyncio.ensure_future(aiopg.connect(**POSTGRES_CONFIG))
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert all(conn.closed for conn in connections)
            finally:
                for conn in connections:
                    conn.close()
                    # Closing before the first await leaves the initialization waiter unconsumed.
                    if conn._waiter is not None and conn._waiter.done() and not conn._waiter.cancelled():
                        conn._waiter.exception()
                await asyncio.sleep(0)

    @pytest.mark.skipif(
        parse_version(aiopg.__version__) < (1, 0),
        reason="aiopg 0.16 pools use the asyncio.Condition protocol removed in Python 3.9",
    )
    @mark_asyncio
    async def test_pool_context_manager(self):
        async with aiopg.create_pool(minsize=1, maxsize=1, **POSTGRES_CONFIG) as pool:
            with pytest.raises(ValueError, match="release connection"):
                async with pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT 1")
                    raise ValueError("release connection")
            assert cursor.closed
            assert not conn.closed
            assert pool.freesize == 1
            async with pool.acquire() as reused_conn:
                assert reused_conn is conn
                async with reused_conn.cursor() as cursor:
                    await cursor.execute("SELECT 2")
                    assert await cursor.fetchone() == (2,)
            assert pool.freesize == 1
            assert [span.resource for span in self.pop_spans()] == ["SELECT 1", "SELECT 2"]
        assert pool.closed
        assert conn.closed

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_user_specified_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The aiopg integration uses its default database service.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = await self._get_conn()
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service == "postgres"

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_user_specified_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The aiopg integration should use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = await self._get_conn()
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service == "mysvc"

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_unspecified_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The aiopg integration should use it.
        """
        # Ensure that the service name was configured
        conn = await self._get_conn()
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_trace_span_name_v0_schema(self):
        conn = await self._get_conn()
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].name == "postgres.query"

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_trace_span_name_v1_schema(self):
        conn = await self._get_conn()
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].name == "postgresql.query"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(DD_AIOPG_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    async def test_user_specified_service_integration_v0(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(DD_AIOPG_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    async def test_user_specified_service_integration_v1(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_user_specified_service_env_var_v0(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "postgres"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_user_specified_service_env_var_v1(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_span_name_v0_schema(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "postgres.query"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_span_name_v1_schema(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "postgresql.query"


class AiopgAnalyticsTestCase(AiopgTestCase):
    async def trace_spans(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("select 'foobar'")
        rows = await cursor.fetchall()
        assert rows

        return self.get_spans()

    async def _test_cursor_ctx_manager(self):
        conn = await self._get_conn()
        cur = await conn.cursor()
        t = type(cur)

        async with conn.cursor() as cur:
            assert t == type(cur), "%s != %s" % (t, type(cur))
            await cur.execute(query="select 'blah'")
            rows = await cur.fetchall()
            assert len(rows) == 1
            assert rows[0][0] == "blah"

        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "postgres.query"

    @mark_asyncio
    async def test_cursor_ctx_manager(self):
        # ensure cursors work with context managers
        # https://github.com/DataDog/dd-trace-py/issues/228
        await self._test_cursor_ctx_manager()
