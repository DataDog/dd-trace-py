import asyncio
import time

import aiopg
from psycopg2 import extras

# project
from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.aiopg.patch import patch
from ddtrace.contrib.aiopg.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
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
            yield from db.execute("select 'foobar'")
        except AttributeError:
            pass

        # Ensure we can run a query and it's correctly traced
        q = "select 'foobarblah'"
        start = time.time()
        cursor = yield from db.cursor()
        yield from cursor.execute(q)
        rows = yield from cursor.fetchall()
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
        assert span.error == 0
        assert span.span_type == "sql"
        assert start <= span.start <= end
        assert span.duration <= end - start
        assert span.get_tag("component") == "aiopg"
        assert span.get_tag("span.kind") == "client"

        # Ensure OpenTracing compatibility
        ot_tracer = init_tracer("aiopg_svc", tracer)
        with ot_tracer.start_active_span("aiopg_op"):
            cursor = yield from db.cursor()
            yield from cursor.execute(q)
            rows = yield from cursor.fetchall()
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
        assert dd_span.error == 0
        assert dd_span.span_type == "sql"
        assert dd_span.get_tag("component") == "aiopg"
        assert span.get_tag("span.kind") == "client"

        # run a query with an error and ensure all is well
        q = "select * from some_non_existant_table"
        cur = yield from db.cursor()
        try:
            yield from cur.execute(q)
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
        assert span.error == 1
        assert span.get_metric("network.destination.port") == TEST_PORT
        assert span.span_type == "sql"
        assert span.get_tag("component") == "aiopg"
        assert span.get_tag("span.kind") == "client"

    @mark_asyncio
    def test_disabled_execute(self):
        conn, tracer = yield from self._get_conn_and_tracer()
        tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        yield from (yield from conn.cursor()).execute(query="select 'blah'")
        yield from (yield from conn.cursor()).execute("select 'blah'")
        assert not self.pop_spans()

    @mark_asyncio
    def test_manual_wrap_extension_types(self):
        conn, _ = yield from self._get_conn_and_tracer()
        # NOTE: this will crash if it doesn't work.
        #   _ext.register_type(_ext.UUID, conn_or_curs)
        #   TypeError: argument 2 must be a connection, cursor or None
        extras.register_uuid(conn_or_curs=conn)

    @mark_asyncio
    def test_connect_factory(self):
        services = ["db", "another"]
        for service in services:
            conn, _ = yield from self._get_conn_and_tracer()
            Pin.get_from(conn).clone(service=service, tracer=self.tracer).onto(conn)
            yield from self.assert_conn_is_traced(self.tracer, conn, service)
            conn.close()

    @mark_asyncio
    def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        service = "fo"

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert not spans, spans

        # Test patch again
        patch()

        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The aiopg integration does use it.

            **note** Unlike other integrations, the current behavior already matches v1 behavior,
            as the service specified by aiopg is overwritten with `service=None` by psycopg
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
        assert spans[0].service == "mysvc"

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The aiopg integration should use it.
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
        assert spans[0].service == "mysvc"

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_unspecified_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The aiopg integration should use it.
        """
        # Ensure that the service name was configured
        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service == DEFAULT_SPAN_SERVICE_NAME

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_trace_span_name_v0_schema(self):
        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].name == "postgres.query"

    @mark_asyncio
    @run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_trace_span_name_v1_schema(self):
        conn = yield from aiopg.connect(**POSTGRES_CONFIG)
        Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'blah'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].name == "postgresql.query"


class AiopgAnalyticsTestCase(AiopgTestCase):
    @asyncio.coroutine
    def trace_spans(self):
        conn, _ = yield from self._get_conn_and_tracer()

        Pin.get_from(conn).clone(service="db", tracer=self.tracer).onto(conn)

        cursor = yield from conn.cursor()
        yield from cursor.execute("select 'foobar'")
        rows = yield from cursor.fetchall()
        assert rows

        return self.get_spans()

    @mark_asyncio
    def test_analytics_default(self):
        spans = yield from self.trace_spans()
        self.assertEqual(len(spans), 1)
        self.assertIsNone(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    @mark_asyncio
    def test_analytics_with_rate(self):
        with self.override_config("aiopg", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            spans = yield from self.trace_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 0.5)

    @mark_asyncio
    def test_analytics_without_rate(self):
        with self.override_config("aiopg", dict(analytics_enabled=True)):
            spans = yield from self.trace_spans()
            self.assertEqual(len(spans), 1)
            self.assertEqual(spans[0].get_metric(ANALYTICS_SAMPLE_RATE_KEY), 1.0)
