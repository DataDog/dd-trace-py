# stdlib
import time

# 3p
import aiopg
from psycopg2 import extras

# project
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.aiopg.patch import patch, unpatch, AIOPG_1X
from ddtrace import Pin

# testing
from tests.opentracer.utils import init_tracer
from tests.contrib.config import POSTGRES_CONFIG
from tests.tracer.test_tracer import get_dummy_tracer
from tests.contrib.asyncio.utils import AsyncioTestCase, mark_asyncio
from ...subprocesstest import run_in_subprocess
from ... import assert_is_measured

TEST_PORT = POSTGRES_CONFIG['port']


class CursorCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        if AIOPG_1X:
            self._cursor = self._conn.cursor()
            cursor = await self._cursor.__aenter__()
        else:
            cursor = self._cursor = await self._conn.cursor()

        return cursor

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if AIOPG_1X:
            await self._cursor.__aexit__(exc_type, exc_val, exc_tb)
        else:
            self._cursor.close()


class ConnCtx:
    def __init__(self, tracer):
        self._tracer = tracer

    async def __aenter__(self) -> aiopg.Connection:
        if AIOPG_1X:
            self._conn = aiopg.connect(**POSTGRES_CONFIG)
            conn = await self._conn.__aenter__()
        else:
            conn = self._conn = await aiopg.connect(**POSTGRES_CONFIG)

        if self._tracer:
            Pin.get_from(conn).clone(tracer=self._tracer).onto(conn)

        return conn

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self._conn:
            return

        if AIOPG_1X:
            await self._conn.__aexit__(exc_type, exc_val, exc_tb)
        else:
            self._conn.close()

        self._conn = None


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
        Pin(None, tracer=get_dummy_tracer()).onto(aiopg)
        return ConnCtx(None)

    async def assert_conn_is_traced(self, tracer, conn: aiopg.Connection, service):

        # ensure the trace aiopg client doesn't add non-standard
        # methods
        try:
            await conn.execute('select \'foobar\'')
        except AttributeError:
            pass

        writer = tracer.writer
        # Ensure we can run a query and it's correctly traced
        q = 'select \'foobarblah\''
        start = time.time()
        async with CursorCtx(conn) as cursor:
            await cursor.execute(q)
            rows = await cursor.fetchall()

        end = time.time()
        assert rows == [('foobarblah',)]
        assert rows
        spans = writer.pop()
        assert spans
        assert len(spans) == 2

        # execute
        span = spans[0]
        assert_is_measured(span)
        assert span.name == 'postgres.execute'
        assert span.service == service
        assert span.error == 0
        assert span.span_type == 'sql'
        assert start <= span.start <= end
        assert span.duration <= end - start

        span = spans[1]
        assert_is_measured(span)
        assert span.name == 'postgres.fetchall'
        assert span.service == service
        assert span.error == 0
        assert span.span_type == 'sql'
        assert start <= span.start <= end
        assert span.duration <= end - start

        # Ensure OpenTracing compatibility
        ot_tracer = init_tracer('aiopg_svc', tracer)
        with ot_tracer.start_active_span('aiopg_op'):
            async with CursorCtx(conn) as cursor:
                await cursor.execute(q)
                rows = await cursor.fetchall()
            assert rows == [('foobarblah',)]
        spans = writer.pop()
        assert len(spans) == 3
        ot_span, dd_execute_span, dd_fetchall_span = spans
        # confirm the parenting
        assert ot_span.parent_id is None
        assert ot_span.name == 'aiopg_op'
        assert ot_span.service == 'aiopg_svc'

        assert dd_execute_span.parent_id == ot_span.span_id
        assert dd_execute_span.name == 'postgres.execute'
        assert dd_execute_span.resource == q
        assert dd_execute_span.service == service
        assert dd_execute_span.error == 0
        assert dd_execute_span.span_type == 'sql'

        assert dd_fetchall_span.parent_id == ot_span.span_id
        assert dd_fetchall_span.name == 'postgres.fetchall'
        assert dd_fetchall_span.resource == q
        assert dd_fetchall_span.service == service
        assert dd_fetchall_span.error == 0
        assert dd_fetchall_span.span_type == 'sql'

        # run a query with an error and ensure all is well
        q = 'select * from some_non_existant_table'

        async with CursorCtx(conn) as cur:
            try:
                await cur.execute(q)
            except Exception:
                pass
            else:
                assert 0, 'should have an error'

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 1
        span = spans[0]
        assert span.name == 'postgres.execute'
        assert span.service == service
        assert span.error == 1
        # assert span.meta['out.host'] == 'localhost'
        assert span.metrics['out.port'] == TEST_PORT
        assert span.span_type == 'sql'

    @mark_asyncio
    async def test_disabled_execute(self):
        async with self._get_conn() as conn:
            self.tracer.enabled = False
            # these calls were crashing with a previous version of the code.
            async with CursorCtx(conn) as cursor:
                await cursor.execute(operation='select \'blah\'')

            async with CursorCtx(conn) as cursor:
                await cursor.execute('select \'blah\'')

        assert not self.tracer.writer.pop()

    @mark_asyncio
    async def test_manual_wrap_extension_types(self):
        async with self._get_conn() as conn:
            # NOTE: this will crash if it doesn't work.
            #   _ext.register_type(_ext.UUID, conn_or_curs)
            #   TypeError: argument 2 must be a connection, cursor or None
            extras.register_uuid(conn_or_curs=conn)

    @mark_asyncio
    async def test_connect_factory(self):
        tracer = get_dummy_tracer()

        services = ['db', 'another']
        for service in services:
            async with self._get_conn() as conn:
                Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
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

        service = 'fo'

        async with self._get_conn() as conn:
            Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
            async with CursorCtx(conn) as cursor:
                await cursor.execute('select \'blah\'')

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        async with self._get_conn() as conn, CursorCtx(conn) as cursor:
            await cursor.execute('select \'blah\'')

        spans = writer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        async with self._get_conn() as conn:
            Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
            async with CursorCtx(conn) as cursor:
                await cursor.execute('select \'blah\'')

        spans = writer.pop()
        assert spans, spans
        assert len(spans) == 1

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    @mark_asyncio
    async def test_user_specified_service(self):
        """
        When a user specifies a service for the app
            The aiopg integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config
        assert config.service == "mysvc"

        async with self._get_conn() as conn:
            Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)
            async with CursorCtx(conn) as cursor:
                await cursor.execute('select \'blah\'')

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service != "mysvc"


class AiopgAnalyticsTestCase(AiopgTestCase):
    async def trace_spans(self):
        async with self._get_conn() as conn:
            Pin.get_from(conn).clone(service='db', tracer=self.tracer).onto(conn)

            async with CursorCtx(conn) as cursor:
                await cursor.execute('select \'foobar\'')
                rows = await cursor.fetchall()
                assert rows

        return self.get_spans()

    @mark_asyncio
    async def test_analytics_default(self):
        spans = await self.trace_spans()
        assert len(spans) == 2
        assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) is None

    @mark_asyncio
    async def test_analytics_with_rate(self):
        with self.override_config(
            'aiopg',
            dict(analytics_enabled=True, analytics_sample_rate=0.5)
        ):
            spans = await self.trace_spans()
            assert len(spans) == 2
            assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 0.5

    @mark_asyncio
    async def test_analytics_without_rate(self):
        with self.override_config(
            'aiopg',
            dict(analytics_enabled=True)
        ):
            spans = await self.trace_spans()
            assert len(spans) == 2
            assert spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY) == 1.0
