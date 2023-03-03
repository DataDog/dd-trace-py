import time

import aiopg
from psycopg2 import extras
import pytest

# project
from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.aiopg.patch import patch
from ddtrace.contrib.aiopg.patch import unpatch
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.config import POSTGRES_CONFIG
from tests.opentracer.utils import init_tracer
from tests.subprocesstest import run_in_subprocess
from tests.utils import assert_is_measured


TEST_PORT = POSTGRES_CONFIG["port"]


class AiopgTestCase(AsyncioTestCase):
    # default service
    TEST_SERVICE = "postgres"

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
        conn = self._conn = await aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)

        return conn, self.tracer

    async def assert_conn_is_traced(self, tracer, db, service):

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
        assert span.service == service
        assert span.get_tag("sql.query") == q
        assert span.error == 0
        assert span.span_type == "sql"
        assert start <= span.start <= end
        assert span.duration <= end - start
        assert span.get_tag("component") == "aiopg"

        # Ensure OpenTracing compatibility
        ot_tracer = init_tracer("aiopg_svc", tracer)
        with ot_tracer.start_active_span("aiopg_op"):
            cursor = await db.cursor()
            await cursor.execute(q)
            rows = await cursor.fetchall()
            assert rows == [("foobarblah",)]
        spans = self.pop_spans()
        assert len(spans) == 2
        ot_span, dd_span = spans
        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id
        assert ot_span.name == "aiopg_op"
        assert ot_span.service == "aiopg_svc"
        assert dd_span.name == "postgres.query"
        assert dd_span.resource == q
        assert dd_span.service == service
        assert dd_span.get_tag("sql.query") == q
        assert dd_span.error == 0
        assert dd_span.span_type == "sql"
        assert dd_span.get_tag("component") == "aiopg"

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
        assert span.service == service
        assert span.get_tag("sql.query") == q
        assert span.error == 1
        assert span.get_metric("network.destination.port") == TEST_PORT
        assert span.span_type == "sql"
        assert span.get_tag("component") == "aiopg"

    @pytest.mark.asyncio
    async def test_disabled_execute(self):
        conn, tracer = await self._get_conn_and_tracer()
        tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        await (await conn.cursor()).execute(query="select 'blah'")
        await (await conn.cursor()).execute("select 'blah'")
        assert not self.pop_spans()

    @pytest.mark.asyncio
    async def test_manual_wrap_extension_types(self):
        conn, _ = await self._get_conn_and_tracer()
        # NOTE: this will crash if it doesn't work.
        #   _ext.register_type(_ext.UUID, conn_or_curs)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_uuid(conn_or_curs=conn)

    @pytest.mark.asyncio
    async def test_connect_factory(self):
        services = ["db", "another"]
        for service in services:
            conn, _ = await self._get_conn_and_tracer()
            Pin.get_from(conn).clone(service=service, tracer=self.tracer).onto(conn)
            await self.assert_conn_is_traced(self.tracer, conn, service)
            conn.close()

    async def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        service = "fo"

        conn = await aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=self.tracer).onto(conn)
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        conn = await aiopg.connect(**POSTGRES_CONFIG)
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert not spans, spans

        # Test patch again
        patch()

        conn = await aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=self.tracer).onto(conn)
        await (await conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service(self):
        """
        When a user specifies a service for the app
            The aiopg integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service != "mysvc"


class AiopgAnalyticsTestCase(AiopgTestCase):
    async def trace_spans(self):
        conn, _ = await self._get_conn_and_tracer()

        Pin.get_from(conn).clone(service="db", tracer=self.tracer).onto(conn)

        cursor = await conn.cursor()
        await cursor.execute("select 'foobar'")
        rows = await cursor.fetchall()
        assert rows

        return self.get_spans()

    @pytest.mark.asyncio
    async def test_analytics_default(self):
        spans = await self.trace_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    @pytest.mark.asyncio
    async def test_analytics_with_rate(self):
        with self.override_config("aiopg", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            spans = await self.trace_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    @pytest.mark.asyncio
    async def test_analytics_without_rate(self):
        with self.override_config("aiopg", dict(analytics_enabled=True)):
            spans = await self.trace_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)
