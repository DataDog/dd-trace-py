from typing import Generator

import asyncpg
import pytest
import pytest_asyncio

from ddtrace import Pin
from ddtrace.contrib.asyncpg import patch
from ddtrace.contrib.asyncpg import unpatch
from tests.contrib.config import POSTGRES_CONFIG


@pytest.fixture(autouse=True)
def patch_asyncpg():
    # type: () -> Generator[None, None, None]
    patch()
    yield
    unpatch()


@pytest_asyncio.fixture
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
@pytest.mark.snapshot(ignores=["meta.error.stack"])  # stack is noisy between releases
async def test_bad_connect():
    with pytest.raises(OSError):
        conn = await asyncpg.connect(
            host="localhost",
            port=POSTGRES_CONFIG["port"] + 1,
        )
        await conn.close()


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
@pytest.mark.snapshot
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
@pytest.mark.snapshot
async def test_service_override_pin(patched_conn):
    Pin.override(patched_conn, service="custom-svc")
    await patched_conn.execute("SELECT 1")
