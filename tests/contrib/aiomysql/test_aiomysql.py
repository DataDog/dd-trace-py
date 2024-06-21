import os

import aiomysql
import mock
import pymysql
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.aiomysql import patch
from ddtrace.contrib.aiomysql import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.contrib import shared_tests_async as shared_tests
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

    async def _get_conn_tracer(self, tags=None):
        tags = tags if tags is not None else {}

        if not self.conn:
            self.conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
            assert not self.conn.closed
            # Ensure that the default pin is there, with its default value
            pin = Pin.get_from(self.conn)
            assert pin
            # Customize the service
            # we have to apply it on the existing one since new one won't inherit `app`
            pin.clone(tracer=self.tracer, tags={**tags, **pin.tags}).onto(self.conn)

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
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(DD_AIOMYSQL_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    async def test_user_specified_service_integration_v0(self):
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(DD_AIOMYSQL_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    async def test_user_specified_service_integration_v1(self):
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_user_specified_service_v0(self):
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysql"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_user_specified_service_v1(self):
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_unspecified_service_v1(self):
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_span_name_v0_schema(self):
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "mysql.query"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_span_name_v1_schema(self):
        conn, tracer = await self._get_conn_tracer()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = tracer.pop()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "mysql.query"

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    async def test_aiomysql_dbm_propagation_enabled(self):
        conn, tracer = await self._get_conn_tracer()
        cursor = await conn.cursor()

        await shared_tests._test_dbm_propagation_enabled(tracer, cursor, "mysql")

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
        conn, tracer = await self._get_conn_tracer()
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_with_global_service_name_configured(
            config=AIOMYSQL_CONFIG, db_system="mysql", cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

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
    async def test_aiomysql_dbm_propagation_comment_integration_service_name_override(self):
        """tests if dbm comment is set in mysql"""
        conn, tracer = await self._get_conn_tracer()
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_integration_service_name_override(
            config=AIOMYSQL_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

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
        conn, tracer = await self._get_conn_tracer()
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_pin_service_name_override(
            config=AIOMYSQL_CONFIG, cursor=cursor, conn=conn, tracer=tracer, wrapped_instance=cursor.__wrapped__
        )

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
        conn, tracer = await self._get_conn_tracer()
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_peer_service_enabled(
            config=AIOMYSQL_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

    @mark_asyncio
    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1",
        )
    )
    async def test_aiomysql_dbm_propagation_comment_with_peer_service_tag(self):
        """tests if dbm comment is set in mysql"""
        conn, tracer = await self._get_conn_tracer({"peer.service": "peer_service_name"})
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_with_peer_service_tag(
            config=AIOMYSQL_CONFIG,
            cursor=cursor,
            wrapped_instance=cursor.__wrapped__,
            peer_service_name="peer_service_name",
        )
