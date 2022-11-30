import os

import aiomysql
import pytest

from ddtrace import Pin
from ddtrace import Tracer
from ddtrace.contrib.aiomysql import patch
from ddtrace.contrib.aiomysql import unpatch
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
    with pytest.raises(Exception):
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
async def test_user_specified_service(ddtrace_run_python_code_in_subprocess):
    """
    When a user specifies a service for the app
        The aiomysql integration should not use it.
    """
    env = os.environ.copy()
    env["DD_SERVICE"] = "my-service-name"
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
