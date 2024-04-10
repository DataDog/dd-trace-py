import os

import aiomysql
import mock
import pymysql
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.aiomysql import patch
from ddtrace.contrib.aiomysql import unpatch
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
from tests.contrib.config import MYSQL_CONFIG


AIOMYSQL_CONFIG = dict(MYSQL_CONFIG)
AIOMYSQL_CONFIG["db"] = AIOMYSQL_CONFIG["database"]
del AIOMYSQL_CONFIG["database"]


@pytest.fixture(autouse=True)
def patch_aiomysql():
    patch()
    yield
    unpatch()


@pytest.fixture
async def patched_conn(tracer):
    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    Pin.get_from(conn).clone(tracer=tracer).onto(conn)
    yield conn
    conn.close()


@pytest.fixture()
async def snapshot_conn():
    tracer = Tracer()
    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    Pin.get_from(conn).clone(tracer=tracer).onto(conn)
    yield conn
    conn.close()
    tracer.shutdown()


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.error.stack"])
async def test_queries(snapshot_conn):
    db = snapshot_conn
    q = "select 'Jellysmack'"
    cursor = await db.cursor()
    await cursor.execute(q)
    rows = await cursor.fetchall()
    assert rows == (("Jellysmack",),)

    # run a query with an error and ensure all is well
    q = "select * from some_non_existant_table"
    cur = await db.cursor()
    with pytest.raises(pymysql.err.ProgrammingError):
        await cur.execute(q)


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_pin_override(patched_conn, tracer):
    Pin.override(patched_conn, service="db")
    cursor = await patched_conn.cursor()
    await cursor.execute("SELECT 1")
    rows = await cursor.fetchall()
    assert rows == ((1,),)


@pytest.mark.asyncio
async def test_patch_unpatch(tracer, test_spans):
    # Test patch idempotence
    patch()
    patch()

    service = "fo"

    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
    await (await conn.cursor()).execute("select 'dba4x4'")
    conn.close()

    spans = test_spans.pop()
    assert spans, spans
    assert len(spans) == 1

    # Test unpatch
    unpatch()

    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    await (await conn.cursor()).execute("select 'dba4x4'")
    conn.close()

    spans = test_spans.pop()
    assert not spans, spans

    # Test patch again
    patch()

    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    Pin.get_from(conn).clone(service=service, tracer=tracer).onto(conn)
    await (await conn.cursor()).execute("select 'dba4x4'")
    conn.close()

    spans = test_spans.pop()
    assert spans, spans
    assert len(spans) == 1


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_user_specified_service_v0(ddtrace_run_python_code_in_subprocess):
    """
    v0: When a user specifies a service for the app
        The aiomysql integration should not use it.
    """
    env = os.environ.copy()
    env["DD_SERVICE"] = "my-service-name"
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = "v0"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import asyncio
import aiomysql
from ddtrace import config
from tests.contrib.aiomysql.test_aiomysql import AIOMYSQL_CONFIG

assert config.service == "my-service-name"
async def test():
    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    await (await conn.cursor()).execute("select 'dba4x4'")
    conn.close()
asyncio.run(test())""",
        env=env,
    )
    assert status == 0, err
    assert out == err == b""


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_user_specified_service_v1(ddtrace_run_python_code_in_subprocess):
    """
    v1: When a user specifies a service for the app
        The aiomysql integration should use it.
    """
    env = os.environ.copy()
    env["DD_SERVICE"] = "my-service-name"
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = "v1"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import asyncio
import aiomysql
from ddtrace import config
from tests.contrib.aiomysql.test_aiomysql import AIOMYSQL_CONFIG

assert config.service == "my-service-name"
async def test():
    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    await (await conn.cursor()).execute("select 'dba4x4'")
    conn.close()
asyncio.run(test())""",
        env=env,
    )
    assert status == 0, err
    assert out == err == b""


@pytest.mark.asyncio
@pytest.mark.snapshot
async def test_unspecified_service_v1(ddtrace_run_python_code_in_subprocess):
    """
    v1: When a user specifies nothing for a service,
        it should default to internal.schema.DEFAULT_SPAN_SERVICE_NAME
    """
    env = os.environ.copy()
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = "v1"
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import asyncio
import aiomysql
from ddtrace import config
from tests.contrib.aiomysql.test_aiomysql import AIOMYSQL_CONFIG

async def test():
    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    await (await conn.cursor()).execute("select 'dba4x4'")
    conn.close()
asyncio.run(test())""",
        env=env,
    )
    assert status == 0, err
    assert out == err == b""


@pytest.mark.asyncio
@pytest.mark.snapshot
@pytest.mark.parametrize("version", ["v0", "v1"])
async def test_schematized_span_name(ddtrace_run_python_code_in_subprocess, version):
    """
    When a user specifies a service for the app
        The aiomysql integration should not use it.
    """
    env = os.environ.copy()
    env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(
        """
import asyncio
import aiomysql
from ddtrace import config
from tests.contrib.aiomysql.test_aiomysql import AIOMYSQL_CONFIG
async def test():
    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    await (await conn.cursor()).execute("select 'dba4x4'")
    conn.close()
asyncio.run(test())""",
        env=env,
    )
    assert status == 0, err
    assert out == err == b""


class AioMySQLTestCase(AsyncioTestCase):
    # default service
    TEST_SERVICE = "mysql"
    conn = None

    async def _get_conn_tracer(self):
        if not self.conn:
            self.conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
            assert not self.conn.closed
            # Ensure that the default pin is there, with its default value
            pin = Pin.get_from(self.conn)
            assert pin
            # Customize the service
            # we have to apply it on the existing one since new one won't inherit `app`
            pin.clone(tracer=self.tracer).onto(self.conn)

            return self.conn, self.tracer

    def setUp(self):
        super().setUp()
        self.conn = None
        patch()

    async def tearDown(self):
        super().tearDown()
        if self.conn and not self.conn.closed:
            self.conn.close()

        unpatch()

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    async def test_aiomysql_dbm_propagation_enabled(self):
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "mysql.query"

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
    async def test_aiomysql_dbm_propagation_comment_with_global_service_name_configured(self):
        """tests if dbm comment is set in mysql"""
        db_name = AIOMYSQL_CONFIG["db"]
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()
        # test string queries
        await cursor.execute("select 'blah'")
        await cursor.executemany("select %s", (("foo",), ("bar",)))
        dbm_comment = (
            f"/*dddb='{db_name}',dddbs='orders-app',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
            "ddpv='v7343437-d7ac743'*/ "
        )
        cursor.__wrapped__.execute.assert_called_once_with(dbm_comment + "select 'blah'")
        cursor.__wrapped__.executemany.assert_called_once_with(dbm_comment + "select %s", (("foo",), ("bar",)))
        # test byte string queries
        cursor.__wrapped__.reset_mock()
        await cursor.execute(b"select 'blah'")
        await cursor.executemany(b"select %s", ((b"foo",), (b"bar",)))
        cursor.__wrapped__.execute.assert_called_once_with(dbm_comment.encode() + b"select 'blah'")
        cursor.__wrapped__.executemany.assert_called_once_with(
            dbm_comment.encode() + b"select %s", ((b"foo",), (b"bar",))
        )
        # test composed queries
        cursor.__wrapped__.reset_mock()

    @mark_asyncio
    # @AsyncioTestCase.run_in_subprocess(
    #     env_overrides=dict(
    #         DD_DBM_PROPAGATION_MODE="service",
    #         DD_SERVICE="orders-app",
    #         DD_ENV="staging",
    #         DD_VERSION="v7343437-d7ac743",
    #         DD_AIOMYSQL_SERVICE="service-name-override",
    #     )
    # )
    async def test_aiomysql_dbm_propagation_comment_integration_service_name_override(self):
        """tests if dbm comment is set in mysql"""
        db_name = AIOMYSQL_CONFIG["db"]
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()
        # test string queries
        breakpoint()
        await cursor.execute("select 'blah'")
        await cursor.executemany("select %s", (("foo",), ("bar",)))
        dbm_comment = (
            f"/*dddb='{db_name}',dddbs='service-name-override',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
            "ddpv='v7343437-d7ac743'*/ "
        )
        cursor.__wrapped__.execute.assert_called_once_with(dbm_comment + "select 'blah'")
        cursor.__wrapped__.executemany.assert_called_once_with(dbm_comment + "select %s", (("foo",), ("bar",)))
        # test byte string queries
        cursor.__wrapped__.reset_mock()

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_AIOMYSQL_SERVICE="service-name-override",
        )
    )
    async def test_aiomysql_dbm_propagation_comment_pin_service_name_override(self):
        """tests if dbm comment is set in mysql"""
        db_name = AIOMYSQL_CONFIG["db"]
        conn, tracer = await self._get_conn_tracer()

        Pin.override(patched_conn, service="pin-service-name-override", tracer=tracer)

        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()
        # test string queries
        await cursor.execute("select 'blah'")
        await cursor.executemany("select %s", (("foo",), ("bar",)))
        dbm_comment = (
            f"/*dddb='{db_name}',dddbs='pin-service-name-override',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
            "ddpv='v7343437-d7ac743'*/ "
        )
        cursor.__wrapped__.execute.assert_called_once_with(dbm_comment + "select 'blah'")
        cursor.__wrapped__.executemany.assert_called_once_with(dbm_comment + "select %s", (("foo",), ("bar",)))
        # test byte string queries
        cursor.__wrapped__.reset_mock()

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
    async def test_aiomysql_dbm_propagation_comment_peer_service_enabled(self):
        """tests if dbm comment is set in mysql"""
        db_name = AIOMYSQL_CONFIG["db"]
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()
        # test string queries
        await cursor.execute("select 'blah'")
        await cursor.executemany("select %s", (("foo",), ("bar",)))
        dbm_comment = (
            f"/*dddb='{db_name}',dddbs='test',dde='staging',ddh='127.0.0.1',ddps='orders-app',"
            "ddpv='v7343437-d7ac743'*/ "
        )
        cursor.__wrapped__.execute.assert_called_once_with(dbm_comment + "select 'blah'")
        cursor.__wrapped__.executemany.assert_called_once_with(dbm_comment + "select %s", (("foo",), ("bar",)))
        # test byte string queries
        cursor.__wrapped__.reset_mock()
