import asyncio
import os
from unittest import mock

import aiomysql
import pymysql
import pytest

from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib.internal.aiomysql.patch import AIOTracedCursor
from ddtrace.contrib.internal.aiomysql.patch import patch
from ddtrace.contrib.internal.aiomysql.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.schema.default import DEFAULT_SPAN_SERVICE_NAME
from tests.contrib import shared_tests_async as shared_tests
from tests.contrib.asyncio.utils import AsyncioTestCase
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
    yield conn
    conn.close()


@pytest.fixture()
async def snapshot_conn(tracer):
    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
    yield conn
    conn.close()
    tracer.flush()


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
async def test_query_is_blocked_before_execution() -> None:
    cursor = mock.AsyncMock()
    traced_cursor = AIOTracedCursor(cursor, db_tags={})

    for method in ("execute", "executemany"):
        with mock.patch.object(core, "dispatch_event", side_effect=BlockingException) as dispatch_event:
            with pytest.raises(BlockingException):
                await getattr(traced_cursor, method)("SELECT 1")

        dispatch_event.assert_called_once_with(
            DbQueryEvent(query="SELECT 1", span_name_prefix="mysql", get_encoding=traced_cursor._dbapi_query_encoding)
        )
        getattr(cursor, method).assert_not_awaited()


@pytest.mark.asyncio
async def test_byte_query_uses_connection_encoding():
    for charset, codec in (("utf8mb4", "utf-8"), ("latin1", "latin-1")):
        conn = await aiomysql.connect(**AIOMYSQL_CONFIG, charset=charset)
        try:
            await shared_tests._test_byte_query_event(await conn.cursor(), codec, "mysql")
        finally:
            conn.close()


@pytest.mark.asyncio
async def test_patch_unpatch(tracer, test_spans):
    # Test patch idempotence
    patch()
    patch()

    conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
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
    conn = None

    async def _get_conn(self):
        if not self.conn:
            self.conn = await aiomysql.connect(**AIOMYSQL_CONFIG)
            assert not self.conn.closed

        return self.conn

    def setUp(self):
        super().setUp()
        self.conn = None
        patch()

    async def asyncTearDown(self):
        await super().asyncTearDown()
        if self.conn and not self.conn.closed:
            self.conn.close()

        unpatch()

    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(DD_AIOMYSQL_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    async def test_user_specified_service_integration_v0(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(DD_AIOMYSQL_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1")
    )
    async def test_user_specified_service_integration_v1(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_user_specified_service_v0(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysql"

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_user_specified_service_v1(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "mysvc"

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_unspecified_service_v1(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    async def test_span_name_v0_schema(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "mysql.query"

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    async def test_span_name_v1_schema(self):
        conn = await self._get_conn()

        cursor = await conn.cursor()
        await cursor.execute("SELECT 1")
        spans = self.pop_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.name == "mysql.query"

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    async def test_aiomysql_dbm_propagation_enabled(self):
        conn = await self._get_conn()
        cursor = await conn.cursor()

        await shared_tests._test_dbm_propagation_enabled(self.tracer, cursor, "mysql")

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
        conn = await self._get_conn()
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_with_global_service_name_configured(
            config=AIOMYSQL_CONFIG, db_system="mysql", cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

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
        conn = await self._get_conn()
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_integration_service_name_override(
            config=AIOMYSQL_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

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
        conn = await self._get_conn()
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_peer_service_enabled(
            config=AIOMYSQL_CONFIG, cursor=cursor, wrapped_instance=cursor.__wrapped__
        )

    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
            DD_TAGS="peer.service:peer_service_name",
            DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1",
        )
    )
    async def test_aiomysql_dbm_propagation_comment_with_peer_service_tag(self):
        """tests if dbm comment is set in mysql"""
        conn = await self._get_conn()
        cursor = await conn.cursor()
        cursor.__wrapped__ = mock.AsyncMock()

        await shared_tests._test_dbm_propagation_comment_with_peer_service_tag(
            config=AIOMYSQL_CONFIG,
            cursor=cursor,
            wrapped_instance=cursor.__wrapped__,
            peer_service_name="peer_service_name",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["execute", "executemany"])
@pytest.mark.parametrize("failure_type", [None, ValueError, asyncio.CancelledError])
async def test_result_metadata_and_parent_restored(tracer, test_spans, method_name, failure_type):
    result = object()
    failure = failure_type("driver execution failed") if failure_type else None
    cursor = mock.AsyncMock(rowcount=0, rownumber=0)
    traced = AIOTracedCursor(cursor, db_tags={"db.system": "mysql", "db.name": "orders"})
    execution_context = core.current

    async def execute(*args, **kwargs):
        assert tracer.current_span().name == "mysql.query"
        # Metadata is populated by the driver even when execution raises.
        cursor.rowcount = 7
        cursor.rownumber = 2
        if failure is not None:
            raise failure
        return result

    method = getattr(cursor, method_name)
    method.side_effect = execute
    with tracer.trace("parent") as parent:
        if failure_type:
            with pytest.raises(failure_type) as caught:
                await getattr(traced, method_name)("SELECT 1")
            assert caught.value is failure
        else:
            assert await getattr(traced, method_name)("SELECT 1") is result
        assert tracer.current_span() is parent
        assert core.current is execution_context

    assert len(test_spans.spans) == 2
    span = test_spans.spans[1]
    assert span.finished
    assert span.parent_id == parent.span_id
    assert span.resource == "SELECT 1"
    assert span.error == int(failure_type is not None)
    assert span.get_tag("db.system") == "mysql"
    assert span.get_tag("db.name") == "orders"
    assert span.get_metric("db.row_count") == 7
    assert span.get_metric("db.rownumber") == 2
    if failure_type:
        assert span.get_tag("error.message") == str(failure)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [None, ValueError, asyncio.CancelledError])
async def test_cleanup_failure_preserves_driver_error(tracer, test_spans, failure_type):
    failure = failure_type("driver failed") if failure_type else None
    cursor = mock.AsyncMock()
    cursor.execute.side_effect = failure
    type(cursor).rowcount = mock.PropertyMock(side_effect=RuntimeError("cursor closed"))
    try:
        raise LookupError("already handled by the application")
    except LookupError:
        with tracer.trace("parent") as parent:
            with pytest.raises(failure_type or RuntimeError) as caught:
                await AIOTracedCursor(cursor, db_tags={}).execute("SELECT 1")
            if failure is not None:
                assert caught.value is failure
            assert tracer.current_span() is parent
    expected = "driver failed" if failure_type else "cursor closed"
    assert test_spans.spans[1].get_tag("error.message") == expected
