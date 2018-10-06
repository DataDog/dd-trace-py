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
from tests.test_tracer import get_dummy_tracer
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
    # def setup_method(self, method):
    #     patch()

    def teardown_method(self, method):
        unpatch()

    def test_execute_metadata(self, test_conn, test_tracer):
        """Metadata related to an `execute` call should be captured."""
        conn, cur = test_conn

        Pin.override(cur, tracer=test_tracer)

        cur.execute(
            "INSERT INTO {} (a, b) VALUES (1, 'aa');".format(TEST_TABLE)
        )
        cur.execute("SELECT * FROM {};".format(TEST_TABLE))
        conn.close()

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

    def test_cursor_override(self, test_conn):
        """Test overriding the tracer with our own."""
        conn, cur = test_conn

        test_tracer = get_dummy_tracer()
        Pin.override(cur, tracer=test_tracer)

        cur.execute(
            "INSERT INTO {} (a, b) VALUES (1, 'aa');".format(TEST_TABLE)
        )
        cur.execute("SELECT * FROM {};".format(TEST_TABLE))
        conn.close()

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

    def test_rowcount_oddity(self, test_conn, test_tracer):
        """Vertica treats rowcount specially. Ensure we handle it.

        See https://github.com/vertica/vertica-python/tree/029a65a862da893e7bd641a68f772019fd9ecc99#rowcount-oddities
        """
        conn, cur = test_conn

        with conn:
            cur.execute(
                """
                INSERT INTO {} (a, b)
                SELECT 1, 'a'
                UNION ALL
                SELECT 2, 'b'
                UNION ALL
                SELECT 3, 'c'
                UNION ALL
                SELECT 4, 'd'
                UNION ALL
                SELECT 5, 'e'
                """.format(TEST_TABLE)
            )
            assert cur.rowcount == -1

            cur.execute("SELECT * FROM {};".format(TEST_TABLE))
            cur.fetchone()
            cur.rowcount == 1
            cur.fetchone()
            cur.rowcount == 2
            # fetchall just calls fetchone for each remaining row
            cur.fetchall()
            cur.rowcount == 5

        spans = test_tracer.writer.pop()
        assert len(spans) == 9

        # check all the rowcounts
        assert spans[0].name == 'vertica.query'
        assert spans[1].get_metric('db.rowcount') == -1
        assert spans[1].name == 'vertica.query'
        assert spans[1].get_metric('db.rowcount') == -1
        assert spans[2].name == 'vertica.fetchone'
        assert spans[2].get_metric('db.rowcount') == 1
        assert spans[3].name == 'vertica.fetchone'
        assert spans[3].get_metric('db.rowcount') == 2
        assert spans[4].name == 'vertica.fetchall'
        assert spans[4].get_metric('db.rowcount') == 5

