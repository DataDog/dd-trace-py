# stdlib

# 3p
import vertica_python
import wrapt

# project
from ddtrace import Pin
from ddtrace.contrib.verticadb.patch import patch, unpatch

# testing
import pytest
from tests.contrib.config import VERTICA_CONFIG
from tests.test_tracer import get_dummy_tracer


TEST_TABLE = 'test_table'


class TestVerticaPatching(object):
    def test_patch(self):
        patch()
        assert issubclass(vertica_python.Connection, wrapt.ObjectProxy)
        assert issubclass(vertica_python.connect, wrapt.ObjectProxy)
        # assert issubclass(vertica_python.vertica.connection.Connection, wrapt.ObjectProxy)

    def test_idempotent_patch(self):
        patch()
        patch()
        assert issubclass(vertica_python.Connection, wrapt.ObjectProxy)
        assert issubclass(vertica_python.connect, wrapt.ObjectProxy)

    def test_unpatch(self):
        patch()
        unpatch()
        assert not issubclass(vertica_python.Connection, wrapt.ObjectProxy)
        # assert not issubclass(vertica_python.connect, wrapt.ObjectProxy)


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
    return conn, cur


class TestVertica(object):
    def setup_method(self, method):
        patch()

    def teardown_method(self, method):
        unpatch()

    def test_connection(self):
        conn = vertica_python.connect(**VERTICA_CONFIG)
        with conn:
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

    def test_execute(self, test_conn):
        conn, cur = test_conn

        tracer = get_dummy_tracer()

        Pin.override(cur, tracer=tracer)

        with conn:
            cur.execute("INSERT INTO {} (a, b) VALUES (1, 'aa'); commit;".format(TEST_TABLE))
            cur.execute("SELECT * FROM {};".format(TEST_TABLE))
            row = [i for i in cur.iterate()][0]
            assert row[0] == 1
            assert row[1] == 'aa'

        spans = tracer.writer.pop()
        assert len(spans) == 2
        assert spans[0].service == 'vertica'
        assert spans[0].span_type == 'vertica'
        assert spans[0].name == 'vertica.query'
        assert spans[0].get_metric('db.rowcount') == -1
        assert spans[0].get_tag('query') == 'INSERT INTO test_table (a, b) VALUES (1, \'aa\'); commit;'
        assert spans[1].get_tag('query') == 'SELECT * FROM test_table;'
