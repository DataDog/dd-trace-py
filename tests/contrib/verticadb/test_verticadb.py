# stdlib

# 3p
import vertica_python
from vertica_python.errors import VerticaSyntaxError
import wrapt

# project
from ddtrace import Pin
from ddtrace.contrib.verticadb.patch import patch, unpatch
from ddtrace.ext import errors

# testing
import pytest
from tests.contrib.config import VERTICA_CONFIG
from .fixtures import test_conn, test_tracer


TEST_TABLE = "test_table"

'''
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

'''
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

    def test_execute_metadata(self, test_conn, test_tracer):
        """Metadata related to an `execute` call should be captured."""
        conn, cur = test_conn

        Pin.override(cur, tracer=test_tracer)

        with conn:
            cur.execute(
                "INSERT INTO {} (a, b) VALUES (1, 'aa');".format(TEST_TABLE)
            )
            cur.execute("SELECT * FROM {};".format(TEST_TABLE))
            row = [i for i in cur.iterate()][0]
            assert row[0] == 1
            assert row[1] == "aa"

        spans = test_tracer.writer.pop()
        assert len(spans) == 2

        # check all the metadata
        assert spans[0].service == "vertica"
        assert spans[0].span_type == "vertica"
        assert spans[0].name == "vertica.query"
        assert spans[0].get_metric("db.rowcount") == -1
        query = "INSERT INTO test_table (a, b) VALUES (1, 'aa');"
        assert spans[0].get_tag("query") == query
        assert spans[0].get_tag("out.host") == "127.0.0.1"
        assert spans[0].get_tag("out.port") == "5433"

        assert spans[1].get_tag("query") == "SELECT * FROM test_table;"

    def test_execute_exception(self, test_conn, test_tracer):
        """Exceptions should result in appropriate span tagging."""
        conn, cur = test_conn

        Pin.override(cur, tracer=test_tracer)

        with conn, pytest.raises(VerticaSyntaxError):
            cur.execute("INVALID QUERY")

        spans = test_tracer.writer.pop()
        assert len(spans) == 2

        # check all the metadata
        assert spans[0].service == "vertica"
        assert spans[0].error == 1
        assert "INVALID QUERY" in spans[0].get_tag(errors.ERROR_MSG)
        assert spans[0].get_tag(errors.ERROR_TYPE) == "vertica_python.errors.VerticaSyntaxError"
        assert spans[0].get_tag(errors.ERROR_STACK)

        assert spans[1].get_tag('query') == 'COMMIT;'
