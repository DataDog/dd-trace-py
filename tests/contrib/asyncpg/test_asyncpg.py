import os
from typing import Generator  # noqa:F401

import asyncpg
import mock
import pytest

from ddtrace.contrib.internal.asyncpg.patch import patch
from ddtrace.contrib.internal.asyncpg.patch import unpatch
from ddtrace.contrib.internal.trace_utils import iswrapped
from ddtrace.trace import Pin
from ddtrace.trace import tracer
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
from tests.contrib.config import POSTGRES_CONFIG


@pytest.fixture(autouse=True)
def patch_asyncpg():
    # type: () -> Generator[None, None, None]
    patch()
    yield
    unpatch()


@pytest.fixture
async def patched_conn():
    # type: () -> Generator[asyncpg.Connection, None, None]
    conn = await asyncpg.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        user=POSTGRES_CONFIG["user"],
        database=POSTGRES_CONFIG["dbname"],
        password=POSTGRES_CONFIG["password"],
    )
    yield conn
    await conn.close()


@pytest.mark.asyncio
async def test_connect(snapshot_context):
    with snapshot_context():
        conn = await asyncpg.connect(
            host=POSTGRES_CONFIG["host"],
            port=POSTGRES_CONFIG["port"],
            user=POSTGRES_CONFIG["user"],
            database=POSTGRES_CONFIG["dbname"],
            password=POSTGRES_CONFIG["password"],
        )
        await conn.close()

    # Using dsn should result in the same trace
    with snapshot_context():
        conn = await asyncpg.connect(
            dsn="postgresql://%s:%s@%s:%s/%s"
            % (
                POSTGRES_CONFIG["user"],
                POSTGRES_CONFIG["password"],
                POSTGRES_CONFIG["host"],
                POSTGRES_CONFIG["port"],
                POSTGRES_CONFIG["dbname"],
            )
        )
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.snapshot(
    ignores=["meta.error.stack", "meta.error.message", "meta.error.type"]
)  # stack is noisy between releases
async def test_bad_connect():
    with pytest.raises(OSError):
        await asyncpg.connect(
            host="localhost",
            port=POSTGRES_CONFIG["port"] + 1,
        )


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_connection_methods(patched_conn):
    status = await patched_conn.execute(
        """
        CREATE TEMP TABLE test (id serial PRIMARY KEY, name varchar(12) NOT NULL UNIQUE);
    """
    )
    assert status == "CREATE TABLE"

    status = await patched_conn.executemany(
        """
        INSERT INTO test (name) VALUES ($1), ($2), ($3);
    """,
        [["val1", "val2", "val3"]],
    )
    assert status is None

    records = await patched_conn.fetch("SELECT * FROM test;")
    assert len(records) == 3

    val = await patched_conn.fetchval("SELECT * FROM test LIMIT 1;", column=1)
    assert val == "val1"

    row = await patched_conn.fetchrow("SELECT * FROM test LIMIT 1;")
    assert len(row) == 2
    assert row["name"] == "val1"


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_select(patched_conn):
    ret = await patched_conn.fetchval("SELECT 1")
    assert ret == 1


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.error.stack"])  # stack is noisy between releases
async def test_bad_query(patched_conn):
    with pytest.raises(asyncpg.exceptions.PostgresSyntaxError):
        await patched_conn.execute("malformed; query;dfaskjfd")


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_cursor(patched_conn):
    await patched_conn.execute(
        """
        CREATE TEMP TABLE test (id serial PRIMARY KEY, name varchar(12) NOT NULL UNIQUE);
    """
    )
    await patched_conn.execute(
        """
        INSERT INTO test (name) VALUES ($1), ($2);
        """,
        "value1",
        "value2",
    )

    records = []
    async with patched_conn.transaction():
        async for r in patched_conn.cursor("SELECT * FROM test;"):
            records.append(r["name"])

    assert records == ["value1", "value2"]


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["resource"])
async def test_cursor_manual(patched_conn):
    async with patched_conn.transaction():
        cur = await patched_conn.cursor("SELECT generate_series(0, 100)")
        await cur.forward(10)
        await cur.fetchrow()
        await cur.fetch(5)


@pytest.mark.asyncio
@pytest.mark.snapshot
@pytest.mark.xfail
async def test_service_override_pin(patched_conn):
    Pin._override(patched_conn, service="custom-svc")
    await patched_conn.execute("SELECT 1")


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_parenting(patched_conn):
    with tracer.trace("parent"):
        await patched_conn.execute("SELECT 1")

    with tracer.trace("parent2"):
        c = patched_conn.execute("SELECT 1")
    await c


@pytest.mark.snapshot(async_mode=False)
def test_configure_service_name_env_v0(ddtrace_run_python_code_in_subprocess):
    code = """
import asyncio
import sys
import asyncpg
from tests.contrib.config import POSTGRES_CONFIG

async def test():
    conn = await asyncpg.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        user=POSTGRES_CONFIG["user"],
        database=POSTGRES_CONFIG["dbname"],
        password=POSTGRES_CONFIG["password"],
    )
    await conn.execute("SELECT 1")
    await conn.close()

asyncio.run(test())
    """
    env = os.environ.copy()
    env["DD_ASYNCPG_SERVICE"] = "global-service-name"
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = "v0"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.snapshot(async_mode=False)
def test_configure_service_name_env_v1(ddtrace_run_python_code_in_subprocess):
    code = """
import asyncio
import sys
import asyncpg
from tests.contrib.config import POSTGRES_CONFIG

async def test():
    conn = await asyncpg.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        user=POSTGRES_CONFIG["user"],
        database=POSTGRES_CONFIG["dbname"],
        password=POSTGRES_CONFIG["password"],
    )
    await conn.execute("SELECT 1")
    await conn.close()

asyncio.run(test())
    """
    env = os.environ.copy()
    env["DD_ASYNCPG_SERVICE"] = "global-service-name"
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = "v1"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.snapshot(async_mode=False)
def test_unspecified_service_name_env_v0(ddtrace_run_python_code_in_subprocess):
    code = """
import asyncio
import sys
import asyncpg
from tests.contrib.config import POSTGRES_CONFIG

async def test():
    conn = await asyncpg.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        user=POSTGRES_CONFIG["user"],
        database=POSTGRES_CONFIG["dbname"],
        password=POSTGRES_CONFIG["password"],
    )
    await conn.execute("SELECT 1")
    await conn.close()

asyncio.run(test())
    """
    env = os.environ.copy()
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = "v0"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.snapshot(async_mode=False)
def test_unspecified_service_name_env_v1(ddtrace_run_python_code_in_subprocess):
    code = """
import asyncio
import sys
import asyncpg
from tests.contrib.config import POSTGRES_CONFIG

async def test():
    conn = await asyncpg.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        user=POSTGRES_CONFIG["user"],
        database=POSTGRES_CONFIG["dbname"],
        password=POSTGRES_CONFIG["password"],
    )
    await conn.execute("SELECT 1")
    await conn.close()

asyncio.run(test())
    """
    env = os.environ.copy()
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = "v1"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


@pytest.mark.snapshot(async_mode=False)
@pytest.mark.parametrize("version", ("v0", "v1"))
def test_span_name_by_schema(ddtrace_run_python_code_in_subprocess, version):
    code = """
import asyncio
import sys
import asyncpg
from tests.contrib.config import POSTGRES_CONFIG

async def test():
    conn = await asyncpg.connect(
        host=POSTGRES_CONFIG["host"],
        port=POSTGRES_CONFIG["port"],
        user=POSTGRES_CONFIG["user"],
        database=POSTGRES_CONFIG["dbname"],
        password=POSTGRES_CONFIG["password"],
    )
    await conn.execute("SELECT 1")
    await conn.close()

asyncio.run(test())
    """
    env = os.environ.copy()
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, err
    assert err == b""


def test_patch_unpatch_asyncpg():
    assert iswrapped(asyncpg.connect)
    assert iswrapped(asyncpg.protocol.Protocol.execute)
    assert iswrapped(asyncpg.protocol.Protocol.bind_execute)
    assert iswrapped(asyncpg.protocol.Protocol.query)
    assert iswrapped(asyncpg.protocol.Protocol.bind_execute_many)
    unpatch()
    assert not iswrapped(asyncpg.connect)
    assert not iswrapped(asyncpg.protocol.Protocol.execute)
    assert not iswrapped(asyncpg.protocol.Protocol.bind_execute)
    assert not iswrapped(asyncpg.protocol.Protocol.query)
    assert not iswrapped(asyncpg.protocol.Protocol.bind_execute_many)


class AsyncPgTestCase(AsyncioTestCase):
    # default service
    TEST_SERVICE = "mysql"
    conn = None

    async def _get_conn_tracer(self):
        if not self.conn:
            self.conn = await asyncpg.connect(
                host=POSTGRES_CONFIG["host"],
                port=POSTGRES_CONFIG["port"],
                user=POSTGRES_CONFIG["user"],
                database=POSTGRES_CONFIG["dbname"],
                password=POSTGRES_CONFIG["password"],
            )
            assert not self.conn.is_closed()
            # Ensure that the default pin is there, with its default value
            pin = Pin.get_from(self.conn)
            assert pin
            # Customize the service
            # we have to apply it on the existing one since new one won't inherit `app`
            pin._clone(tracer=self.tracer).onto(self.conn)

            return self.conn, self.tracer

    def setUp(self):
        super().setUp()
        self.conn = None
        patch()

    async def tearDown(self):
        super().tearDown()
        if self.conn and not self.conn.is_closed():
            await self.conn.close()

        unpatch()

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    async def test_asyncpg_dbm_propagation_enabled(self):
        conn, tracer = await self._get_conn_tracer()

        await conn.execute("SELECT 1")
        spans = tracer.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "postgres.query"

        assert span.get_tag("_dd.dbm_trace_injected") == "true"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    async def test_asyncpg_dbm_propagation_comment_with_global_service_name_configured(self):
        """tests if dbm comment is set in postgres"""
        db_name = POSTGRES_CONFIG["dbname"]
        conn, tracer = await self._get_conn_tracer()

        def mock_func(args, kwargs, sql_pos, sql_kw, sql_with_dbm_tags):
            return args, kwargs

        with mock.patch(
            "ddtrace.propagation._database_monitoring.set_argument_value", side_effect=mock_func
        ) as patched:
            # test string queries
            create_table_query = """
                CREATE TABLE IF NOT EXISTS my_table(
                    my_column text PRIMARY KEY
                )
            """

            await conn.execute(create_table_query)
            dbm_comment = (
                f"/*dddb='{db_name}',dddbs='postgres',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
                "ddpv='v7343437-d7ac743'*/ "
            )
            assert (
                patched.call_args_list[0][0][4] == dbm_comment + create_table_query
            ), f"Expected: {dbm_comment + create_table_query},\nActual: {patched.call_args_list[0][0][4]}"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_ASYNCPG_SERVICE="service-name-override",
        )
    )
    async def test_asyncpg_dbm_propagation_comment_integration_service_name_override(self):
        """tests if dbm comment is set in postgres"""
        db_name = POSTGRES_CONFIG["dbname"]
        conn, tracer = await self._get_conn_tracer()

        def mock_func(args, kwargs, sql_pos, sql_kw, sql_with_dbm_tags):
            return args, kwargs

        with mock.patch(
            "ddtrace.propagation._database_monitoring.set_argument_value", side_effect=mock_func
        ) as patched:
            # test string queries
            create_table_query = """
                CREATE TABLE IF NOT EXISTS my_table(
                    my_column text PRIMARY KEY
                )
            """

            await conn.execute(create_table_query)
            dbm_comment = (
                f"/*dddb='{db_name}',dddbs='service-name-override',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
                "ddpv='v7343437-d7ac743'*/ "
            )
            assert (
                patched.call_args_list[0][0][4] == dbm_comment + create_table_query
            ), f"Expected: {dbm_comment + create_table_query},\nActual: {patched.call_args_list[0][0][4]}"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_ASYNCPG_SERVICE="service-name-override",
        )
    )
    async def test_asyncpg_dbm_propagation_comment_pin_service_name_override(self):
        """tests if dbm comment is set in postgres"""
        db_name = POSTGRES_CONFIG["dbname"]
        conn, tracer = await self._get_conn_tracer()

        Pin._override(conn, service="pin-service-name-override", tracer=tracer)

        def mock_func(args, kwargs, sql_pos, sql_kw, sql_with_dbm_tags):
            return args, kwargs

        with mock.patch(
            "ddtrace.propagation._database_monitoring.set_argument_value", side_effect=mock_func
        ) as patched:
            # test string queries
            create_table_query = """
                CREATE TABLE IF NOT EXISTS my_table(
                    my_column text PRIMARY KEY
                )
            """

            await conn.execute(create_table_query)
            dbm_comment = (
                f"/*dddb='{db_name}',dddbs='pin-service-name-override',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
                "ddpv='v7343437-d7ac743'*/ "
            )
            assert (
                patched.call_args_list[0][0][4] == dbm_comment + create_table_query
            ), f"Expected: {dbm_comment + create_table_query},\nActual: {patched.call_args_list[0][0][4]}"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_TRACE_PEER_SERVICE_DEFAULTS_ENABLED="True",
        )
    )
    async def test_asyncpg_dbm_propagation_comment_peer_service_enabled(self):
        """tests if dbm comment is set in postgres"""
        db_name = POSTGRES_CONFIG["dbname"]
        conn, tracer = await self._get_conn_tracer()

        def mock_func(args, kwargs, sql_pos, sql_kw, sql_with_dbm_tags):
            return args, kwargs

        with mock.patch(
            "ddtrace.propagation._database_monitoring.set_argument_value", side_effect=mock_func
        ) as patched:
            # test string queries
            create_table_query = """
                CREATE TABLE IF NOT EXISTS my_table(
                    my_column text PRIMARY KEY
                )
            """

            await conn.execute(create_table_query)
            dbm_comment = (
                f"/*dddb='{db_name}',dddbs='{db_name}',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
                "ddpv='v7343437-d7ac743'*/ "
            )
            assert (
                patched.call_args_list[0][0][4] == dbm_comment + create_table_query
            ), f"Expected: {dbm_comment + create_table_query},\nActual: {patched.call_args_list[0][0][4]}"
