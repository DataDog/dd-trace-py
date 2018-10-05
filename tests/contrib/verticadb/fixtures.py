# 3p
import vertica_python

# project
from ddtrace.contrib.verticadb.patch import patch, unpatch

# testing
import pytest
from tests.contrib.config import VERTICA_CONFIG
from tests.test_tracer import get_dummy_tracer


TEST_TABLE = "test_table"


@pytest.fixture
def test_tracer():
    return get_dummy_tracer()


@pytest.fixture
def test_conn():
    conn = vertica_python.connect(**VERTICA_CONFIG)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS {}".format(TEST_TABLE))
    cur.execute(
        """CREATE TABLE {} (
        a INT,
        b VARCHAR(32)
        )
        """.format(
            TEST_TABLE
        )
    )
    patch()
    return conn, cur
