# 3p

# project
import ddtrace
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
def test_conn(test_tracer):
    ddtrace.tracer = test_tracer
    patch()

    import vertica_python # must happen AFTER installing with patch()
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
    test_tracer.writer.pop()
    return conn, cur
