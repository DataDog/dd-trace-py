# stdlib
import json
import sys
import time

import mock
import psycopg
from psycopg.sql import SQL
from psycopg.sql import Identifier
from psycopg.sql import Literal
from psycopg.types.json import Jsonb
import pytest

from ddtrace import config
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib.internal.psycopg.async_cursor import Psycopg3TracedAsyncCursor
from ddtrace.contrib.internal.psycopg.patch import patch
from ddtrace.contrib.internal.psycopg.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.utils.version import parse_version
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
from tests.contrib.config import POSTGRES_CONFIG
from tests.utils import assert_is_measured


TEST_PORT = POSTGRES_CONFIG["port"]


class PsycopgCore(AsyncioTestCase):
    def setUp(self):
        super(PsycopgCore, self).setUp()

        patch()

    def tearDown(self):
        super(PsycopgCore, self).tearDown()

        unpatch()

    async def _get_conn(self):
        conn = await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG)
        return conn

    async def test_patch_unpatch(self):
        # Test patch idempotence
        patch()
        patch()

        conn = await self._get_conn()
        await conn.cursor().execute("""select 'blah'""")
        self.assert_structure(dict(name="postgres.query"))
        self.reset()

        # Test unpatch
        unpatch()

        conn = await self._get_conn()
        await conn.cursor().execute("""select 'blah'""")
        self.assert_has_no_spans()

        # Test patch again
        patch()

        conn = await self._get_conn()
        await conn.cursor().execute("""select 'blah'""")
        self.assert_structure(dict(name="postgres.query"))

    async def assert_conn_is_traced_async(self, db):
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

        self.assertEqual(rows, [("foobarblah",)])

        self.assert_structure(
            dict(name="postgres.query", resource=q, error=0, span_type="sql"),
        )
        root = self.get_root_span()
        self.assertIsNone(root.get_tag("sql.query"))
        assert start <= root.start <= end
        assert root.duration <= end - start
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
        conn = await self._get_conn()
        await self.assert_conn_is_traced_async(conn)

    async def test_commit(self):
        conn = await self._get_conn()
        await conn.commit()

        self.assert_structure(dict(name="psycopg.connection.commit"))

    async def test_rollback(self):
        conn = await self._get_conn()
        await conn.rollback()

        self.assert_structure(dict(name="psycopg.connection.rollback"))

    @mark_asyncio
    async def test_composed_query_event_is_stringified(self) -> None:
        cursor = mock.AsyncMock(rowcount=0)
        query = SQL("SELECT 1")
        events: list[DbQueryEvent] = []

        def capture_event(event: DbQueryEvent) -> None:
            events.append(event)

        core.on(DbQueryEvent.event_name, capture_event)
        try:
            await Psycopg3TracedAsyncCursor(cursor, cfg=config.psycopg).execute(query)
        finally:
            core.reset_listeners(DbQueryEvent.event_name, capture_event)

        assert events == [DbQueryEvent(query=query.as_string(cursor), span_name_prefix="postgres")]
        cursor.execute.assert_awaited_once_with(query)

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
        db = await self._get_conn()
        async with db.cursor() as cursor:
            await cursor.execute("""select 'blah'""")
            self.assert_structure(dict(name="postgres.query"))

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
        async with await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG) as conn:
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


@pytest.mark.skipif(
    sys.version_info < (3, 14) or parse_version(psycopg.__version__) < (3, 3),
    reason="psycopg template queries require Python 3.14 and psycopg 3.3",
)
@pytest.mark.parametrize(
    "template_source, normalized",
    [
        ('t"SELECT {payload}"', "SELECT $1"),
        ('t"SELECT {payload:s}"', "SELECT $1"),
        ('t"SELECT {payload:t}"', "SELECT $1"),
        ('t"SELECT {payload:b}"', "SELECT $1"),
        ('t"SELECT {payload:l}"', None),
        ('t"SELECT {Literal(payload):l}"', None),
        ('t"SELECT {SQL("{}").format(Literal(payload)):q}"', None),
        ('t"{fragment:q}{nested:q}, {42} AS {column:i}"', 'SELECT $1 AS "va""lue", $2 AS "raw""name"'),
    ],
)
@pytest.mark.asyncio
async def test_template_query_preserves_parameter_adaptation(template_source, normalized, tracer, test_spans):
    # A consuming adapter must see exactly the same input as uninstrumented execution.
    dumps = mock.Mock(side_effect=lambda values: json.dumps(list(values)))
    payload = Jsonb(iter([1, 2]), dumps=dumps)
    nested = eval('t"{payload} AS {column:i}"', {"payload": payload, "column": Identifier('va"lue')})
    query = eval(
        template_source,
        {
            "payload": payload,
            "Literal": Literal,
            "SQL": SQL,
            "fragment": SQL("SELECT ") + SQL(""),
            "nested": nested,
            "column": 'raw"name',
        },
    )
    events = []
    listener = events.append
    patch()
    core.on(DbQueryEvent.event_name, listener)
    try:
        async with await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query)
                rows = await cursor.fetchall()
                assert rows[0][0] == [1, 2]
                if "nested" in template_source:
                    assert rows[0][1] == 42
    finally:
        core.reset_listeners(DbQueryEvent.event_name, listener)
        unpatch()

    dumps.assert_called_once_with(payload.obj)
    assert events == ([DbQueryEvent(query=normalized, span_name_prefix="postgres")] if normalized else [])
    query_spans = [span for span in test_spans.spans if span.name == "postgres.query"]
    assert len(query_spans) == 1
    if normalized:
        assert query_spans[0].resource == normalized


@pytest.mark.asyncio
async def test_custom_composable_query_is_rendered_only_by_driver(tracer):
    render = mock.Mock(side_effect=[b"SELECT 1", b"SELECT 2"])

    class StatefulSQL(SQL):
        def as_bytes(self, context):
            return render()

    query = StatefulSQL("unused")
    listener = mock.Mock()
    patch()
    core.on(DbQueryEvent.event_name, listener)
    try:
        async with await psycopg.AsyncConnection.connect(**POSTGRES_CONFIG) as connection:
            async with connection.cursor() as cursor:
                await cursor.execute(query)
                assert await cursor.fetchone() == (1,)
    finally:
        core.reset_listeners(DbQueryEvent.event_name, listener)
        unpatch()

    render.assert_called_once_with()
    listener.assert_not_called()
