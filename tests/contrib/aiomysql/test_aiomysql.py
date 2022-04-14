import asyncio
import time

import aiomysql

from ddtrace import Pin
from ddtrace.contrib.aiomysql import patch
from ddtrace.contrib.aiomysql import unpatch
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
from tests.contrib.config import MYSQL_CONFIG
from tests.opentracer.utils import init_tracer
from tests.subprocesstest import run_in_subprocess
from tests.utils import assert_is_measured


AIOMYSQL_CONFIG = dict(MYSQL_CONFIG)
AIOMYSQL_CONFIG['db'] = AIOMYSQL_CONFIG['database']
del AIOMYSQL_CONFIG['database']


class AiomysqlTestCase(AsyncioTestCase):
    # default service
    TEST_SERVICE = "mysql"

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
        conn = self._conn = yield from aiomysql.connect(**AIOMYSQL_CONFIG)
        Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)

        return conn, self.tracer

    @asyncio.coroutine
    def assert_conn_is_traced(self, tracer, db, service):

        # Ensure we can run a query and it's correctly traced
        q = "select 'Jellysmack'"
        start = time.time()
        cursor = yield from db.cursor()
        yield from cursor.execute(q)
        rows = yield from cursor.fetchall()
        end = time.time()
        assert rows == (("Jellysmack",),)
        assert rows
        spans = self.pop_spans()
        assert spans
        assert len(spans) == 1
        span = spans[0]
        assert_is_measured(span)
        assert span.name == "mysql.query"
        assert span.resource == q
        assert span.service == service
        assert span.get_tag("sql.query") == q
        assert span.error == 0
        assert span.span_type == "sql"
        assert start <= span.start <= end
        assert span.duration <= end - start

        # Ensure OpenTracing compatibility
        ot_tracer = init_tracer("aiomysql_svc", tracer)
        with ot_tracer.start_active_span("aiomysql_op"):
            cursor = yield from db.cursor()
            yield from cursor.execute(q)
            rows = yield from cursor.fetchall()
            assert rows == (("Jellysmack",),)
        spans = self.pop_spans()
        assert len(spans) == 2
        ot_span, dd_span = spans
        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id
        assert ot_span.name == "aiomysql_op"
        assert ot_span.service == "aiomysql_svc"
        assert dd_span.name == "mysql.query"
        assert dd_span.resource == q
        assert dd_span.service == service
        assert dd_span.get_tag("sql.query") == q
        assert dd_span.error == 0
        assert dd_span.span_type == "sql"

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
        assert span.name == "mysql.query"
        assert span.resource == q
        assert span.service == service
        assert span.get_tag("sql.query") == q
        assert span.error == 1
        assert span.get_metric("out.port") == AIOMYSQL_CONFIG['port']
        assert span.span_type == "sql"

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

        conn = yield from aiomysql.connect(**AIOMYSQL_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'dba4x4'")
        conn.close()

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        conn = yield from aiomysql.connect(**AIOMYSQL_CONFIG)
        yield from (yield from conn.cursor()).execute("select 'dba4x4'")
        conn.close()

        spans = self.pop_spans()
        assert not spans, spans

        # Test patch again
        patch()

        conn = yield from aiomysql.connect(**AIOMYSQL_CONFIG)
        Pin.get_from(conn).clone(service=service, tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'dba4x4'")
        conn.close()

        spans = self.pop_spans()
        assert spans, spans
        assert len(spans) == 1

    @run_in_subprocess(env_overrides=dict(DD_SERVICE="my-service-name"))
    def test_user_specified_service(self):
        """
        When a user specifies a service for the app
            The aiomysql integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "my-service-name"

        conn = yield from aiomysql.connect(**AIOMYSQL_CONFIG)
        Pin.get_from(conn).clone(tracer=self.tracer).onto(conn)
        yield from (yield from conn.cursor()).execute("select 'dba4x4'")
        conn.close()

        spans = self.get_spans()
        assert spans, spans
        assert len(spans) == 1
        assert spans[0].service != "my-service-name"
