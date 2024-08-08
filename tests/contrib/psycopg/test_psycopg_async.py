# stdlib
import time

import psycopg
from psycopg.sql import SQL
from psycopg.sql import Literal

from ddtrace import Pin
from ddtrace.contrib.psycopg.patch import patch
from ddtrace.contrib.psycopg.patch import unpatch
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.config import POSTGRES_CONFIG
from tests.opentracer.utils import init_tracer
from tests.utils import assert_is_measured


TEST_PORT = POSTGRES_CONFIG["port"]


class PsycopgCore(AsyncioTestCase):
    # default service
    TEST_SERVICE = "postgres"

    def setUp(self):
        super(PsycopgCore, self).setUp()

        patch()

    def tearDown(self):
        super(PsycopgCore, self).tearDown()

        unpatch()

    async def _get_conn(self, service=None):
        print(POSTGRES_CONFIG)
        conn = await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG)
        pin = Pin.get_from(conn)
        if pin:
            pin.clone(service=service, tracer=self.tracer).onto(conn)

        return conn

    async def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        service = "fo"

        conn = await self._get_conn(service=service)
        await conn.cursor().execute("""select 'blah'""")
        self.assert_structure(dict(name="postgres.query", service=service))
        self.reset()

        # Test unpatch
        unpatch()

        conn = await self._get_conn()
        await conn.cursor().execute("""select 'blah'""")
        self.assert_has_no_spans()

        # Test patch again
        patch()

        conn = await self._get_conn(service=service)
        await conn.cursor().execute("""select 'blah'""")
        self.assert_structure(dict(name="postgres.query", service=service))

    async def assert_conn_is_traced_async(self, db, service):
        # ensure the trace pscyopg client doesn't add non-standard
        # methods
        try:
            await db.executemany("select %s", (("str_foo",), ("str_bar",)))
        except AttributeError:
            pass

        # Ensure we can run a query and it's correctly traced
        q = """select 'foobarblah'"""

        start = time.time()
        cursor = db.cursor()
        res = await cursor.execute(q)  # execute now returns the cursor
        self.assertEqual(psycopg.AsyncCursor, type(res))
        rows = await res.fetchall()
        end = time.time()

        self.assertEquals(rows, [("foobarblah",)])

        self.assert_structure(
            dict(name="postgres.query", resource=q, service=service, error=0, span_type="sql"),
        )
        root = self.get_root_span()
        self.assertIsNone(root.get_tag("sql.query"))
        assert start <= root.start <= end
        assert root.duration <= end - start
        # confirm analytics disabled by default
        self.reset()

        # run a query with an error and ensure all is well
        q = """select * from some_non_existant_table"""
        cur = db.cursor()
        try:
            await cur.execute(q)
        except Exception:
            pass
        else:
            assert 0, "should have an error"

        self.assert_structure(
            dict(
                name="postgres.query",
                resource=q,
                service=service,
                error=1,
                span_type="sql",
                meta={
                    "out.host": "127.0.0.1",
                },
                metrics={
                    "network.destination.port": TEST_PORT,
                },
            ),
        )
        root = self.get_root_span()
        assert root.get_tag("component") == "psycopg"
        assert root.get_tag("span.kind") == "client"
        assert_is_measured(root)
        self.assertIsNone(root.get_tag("sql.query"))
        self.reset()

    async def test_opentracing_propagation(self):
        # ensure OpenTracing plays well with our integration
        query = """SELECT 'tracing'"""

        db = await self._get_conn()
        ot_tracer = init_tracer("psycopg-svc", self.tracer)

        with ot_tracer.start_active_span("db.access"):
            cursor = db.cursor()
            await cursor.execute(query)
            rows = await cursor.fetchall()

        self.assertEquals(rows, [("tracing",)])

        self.assert_structure(
            dict(name="db.access", service="psycopg-svc"),
            (dict(name="postgres.query", resource=query, service="postgres", error=0, span_type="sql"),),
        )
        assert_is_measured(self.get_spans()[1])
        self.reset()

        with self.override_config("psycopg", dict(trace_fetch_methods=True)):
            db = await self._get_conn()
            ot_tracer = init_tracer("psycopg-svc", self.tracer)

            with ot_tracer.start_active_span("db.access"):
                cursor = db.cursor()
                await cursor.execute(query)
                rows = await cursor.fetchall()

            self.assertEquals(rows, [("tracing",)])

            self.assert_structure(
                dict(name="db.access", service="psycopg-svc"),
                (
                    dict(name="postgres.query", resource=query, service="postgres", error=0, span_type="sql"),
                    dict(name="postgres.query.fetchall", resource=query, service="postgres", error=0, span_type="sql"),
                ),
            )
            assert_is_measured(self.get_spans()[1])

    async def test_cursor_ctx_manager(self):
        # ensure cursors work with context managers
        # https://github.com/DataDog/dd-trace-py/issues/228
        conn = await self._get_conn()
        t = type(conn.cursor())
        async with conn.cursor() as cur:
            assert t == type(cur), "{} != {}".format(t, type(cur))
            await cur.execute(query="""select 'blah'""")
            rows = await cur.fetchall()
            assert len(rows) == 1, rows
            assert rows[0][0] == "blah"

        assert_is_measured(self.get_root_span())
        self.assert_structure(
            dict(name="postgres.query"),
        )

    async def test_disabled_execute(self):
        conn = await self._get_conn()
        self.tracer.enabled = False
        # these calls were crashing with a previous version of the code.
        await conn.cursor().execute(query="""select 'blah'""")
        await conn.cursor().execute("""select 'blah'""")
        self.assert_has_no_spans()

    async def test_connect_factory(self):
        services = ["db", "another"]
        for service in services:
            conn = await self._get_conn(service=service)
            await self.assert_conn_is_traced_async(conn, service)

    async def test_commit(self):
        conn = await self._get_conn()
        await conn.commit()

        self.assert_structure(dict(name="psycopg.connection.commit", service=self.TEST_SERVICE))

    async def test_rollback(self):
        conn = await self._get_conn()
        await conn.rollback()

        self.assert_structure(dict(name="psycopg.connection.rollback", service=self.TEST_SERVICE))

    async def test_composed_query(self):
        """Checks whether execution of composed SQL string is traced"""
        query = SQL(" union all ").join(
            [SQL("""select {} as x""").format(Literal("one")), SQL("""select {} as x""").format(Literal("two"))]
        )
        db = await self._get_conn()

        async with db.cursor() as cur:
            await cur.execute(query=query)
            rows = await cur.fetchall()
            assert len(rows) == 2, rows
            assert rows[0][0] == "one"
            assert rows[1][0] == "two"

        assert_is_measured(self.get_root_span())
        self.assert_structure(
            dict(name="postgres.query", resource=query.as_string(db)),
        )

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_user_specified_app_service_v0(self):
        """
        v0: When a user specifies a service for the app
            The psycopg integration should not use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = await self._get_conn()
        await conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service != "mysvc"

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_user_specified_app_service_v1(self):
        """
        v1: When a user specifies a service for the app
            The psycopg integration should use it.
        """
        # Ensure that the service name was configured
        from ddtrace import config

        assert config.service == "mysvc"

        conn = await self._get_conn()
        await conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].service == "mysvc"

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_span_name_v0_schema(self):
        conn = await self._get_conn()
        await conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].name == "postgres.query"

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_span_name_v1_schema(self):
        conn = await self._get_conn()
        await conn.cursor().execute("""select 'blah'""")

        spans = self.get_spans()
        self.assertEqual(len(spans), 1)
        assert spans[0].name == "postgresql.query"

    async def test_contextmanager_connection(self):
        service = "fo"
        db = await self._get_conn(service=service)
        async with db.cursor() as cursor:
            await cursor.execute("""select 'blah'""")
            self.assert_structure(dict(name="postgres.query", service=service))

    async def test_connection_execute(self):
        """Checks whether connection execute shortcute method works as normal"""

        query = SQL("""select 'one' as x""")
        conn = await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG)
        cur = await conn.execute(query)

        rows = await cur.fetchall()
        assert len(rows) == 1, rows
        assert rows[0][0] == "one"

    async def test_connection_context_execute(self):
        """Checks whether connection context manager works as normal."""

        query = SQL("""select 'one' as x""")
        async with (await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG)) as conn:
            cur = await conn.execute(query)
            rows = await cur.fetchall()

            assert len(rows) == 1, rows
            assert rows[0][0] == "one"

    async def test_cursor_context_execute(self):
        """Checks whether cursor context manager works as normal."""

        query = SQL("""select 'one' as x""")
        async with (await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG)).cursor() as cur:
            await cur.execute(query)
            rows = await cur.fetchall()

            assert len(rows) == 1, rows
            assert rows[0][0] == "one"

    async def test_cursor_from_connection_shortcut(self):
        """Checks whether connection execute shortcute method works as normal"""

        query = SQL("""select 'one' as x""")
        conn = await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG)

        cur = psycopg.AsyncCursor(connection=conn)
        await cur.execute(query)

        rows = await cur.fetchall()
        assert len(rows) == 1, rows
        assert rows[0][0] == "one"

    async def test_cursor_async_connect_execute(self):
        """Checks whether connection can execute operations with async iteration."""

        async with psycopg.AsyncConnection.connect(**POSTGRES_CONFIG) as conn:
            async with conn.cursor() as cur:
                await cur.execute("""select 'one' as x""")
                await cur.execute("""select 'blah'""")

                async for row in cur:
                    spans = self.get_spans()
                    assert len(spans) == 2
                    assert spans[0].name == "postgres.query"
                    assert spans[0].resource == "select ?"
                    assert spans[0].service == "postgres"
                    assert spans[1].name == "postgres.query"
                    assert spans[1].resource == "select ?"
                    assert spans[1].service == "postgres"
