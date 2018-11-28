# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# flake8: noqa
# stdlib

# 3p
import wrapt

# project
from ddtrace import Pin
from ddtrace.contrib.vertica.patch import patch, unpatch
from ddtrace.ext import errors

# testing
import pytest
from tests.contrib.config import VERTICA_CONFIG
from tests.opentracer.utils import init_tracer
from tests.test_tracer import get_dummy_tracer

from .fixtures import test_conn, test_tracer, TEST_TABLE
from .utils import override_config


class TestVerticaPatching(object):
    def test_not_patched(self):
        """Ensure that vertica is not patched somewhere before our tests."""
        import vertica_python

        assert not isinstance(vertica_python.Connection.cursor, wrapt.ObjectProxy)
        assert not isinstance(
            vertica_python.vertica.cursor.Cursor.execute, wrapt.ObjectProxy
        )

    def test_patch_after_import(self):
        """Patching _after_ the import will not work because we hook into
        the module import system.

        Vertica uses a local reference to `Cursor` which won't get patched
        if we call `patch` after the module has already been imported.
        """
        import vertica_python

        assert not isinstance(vertica_python.vertica.connection.Connection.cursor, wrapt.ObjectProxy)
        assert not isinstance(
            vertica_python.vertica.cursor.Cursor.execute, wrapt.ObjectProxy
        )

        patch()

        conn = vertica_python.connect(**VERTICA_CONFIG)
        cursor = conn.cursor()
        assert not isinstance(cursor, wrapt.ObjectProxy)

    def test_patch_before_import(self):
        patch()
        import vertica_python

        # use a patched method from each class as indicators
        assert isinstance(vertica_python.Connection.cursor, wrapt.ObjectProxy)
        assert isinstance(
            vertica_python.vertica.cursor.Cursor.execute, wrapt.ObjectProxy
        )

    def test_idempotent_patch(self):
        patch()
        patch()
        import vertica_python

        assert not isinstance(
            vertica_python.Connection.cursor.__wrapped__, wrapt.ObjectProxy
        )
        assert not isinstance(
            vertica_python.vertica.cursor.Cursor.execute.__wrapped__, wrapt.ObjectProxy
        )
        assert isinstance(vertica_python.Connection.cursor, wrapt.ObjectProxy)
        assert isinstance(
            vertica_python.vertica.cursor.Cursor.execute, wrapt.ObjectProxy
        )

    def test_unpatch_before_import(self):
        patch()
        unpatch()
        import vertica_python

        assert not isinstance(vertica_python.Connection.cursor, wrapt.ObjectProxy)
        assert not isinstance(
            vertica_python.vertica.cursor.Cursor.execute, wrapt.ObjectProxy
        )

    def test_unpatch_after_import(self):
        patch()
        import vertica_python

        unpatch()
        assert not isinstance(vertica_python.Connection.cursor, wrapt.ObjectProxy)
        assert not isinstance(
            vertica_python.vertica.cursor.Cursor.execute, wrapt.ObjectProxy
        )


class TestVertica(object):
    def teardown_method(self, method):
        unpatch()

    @override_config({"service_name": "test_svc_name"})
    def test_configuration_service_name(self):
        """Ensure that the integration can be configured."""
        patch()
        import vertica_python

        test_tracer = get_dummy_tracer()

        conn = vertica_python.connect(**VERTICA_CONFIG)
        cur = conn.cursor()
        Pin.override(cur, tracer=test_tracer)
        with conn:
            cur.execute("DROP TABLE IF EXISTS {}".format(TEST_TABLE))
        spans = test_tracer.writer.pop()
        assert len(spans) == 1
        assert spans[0].service == "test_svc_name"

    @override_config(
        {
            "patch": {
                "vertica_python.vertica.connection.Connection": {
                    "routines": {
                        "cursor": {
                            "operation_name": "get_cursor",
                            "trace_enabled": True,
                        }
                    }
                }
            }
        }
    )
    def test_configuration_routine(self):
        """Ensure that the integration routines can be configured."""
        patch()
        import vertica_python

        test_tracer = get_dummy_tracer()

        conn = vertica_python.connect(**VERTICA_CONFIG)
        Pin.override(conn, service="mycustomservice", tracer=test_tracer)
        conn.cursor()  # should be traced now
        conn.close()
        spans = test_tracer.writer.pop()
        assert len(spans) == 1
        assert spans[0].name == "get_cursor"
        assert spans[0].service == "mycustomservice"

    def test_execute_metadata(self, test_conn, test_tracer):
        """Metadata related to an `execute` call should be captured."""
        conn, cur = test_conn

        Pin.override(cur, tracer=test_tracer)

        with conn:
            cur.execute("INSERT INTO {} (a, b) VALUES (1, 'aa');".format(TEST_TABLE))
            cur.execute("SELECT * FROM {};".format(TEST_TABLE))

        spans = test_tracer.writer.pop()
        assert len(spans) == 2

        # check all the metadata
        assert spans[0].service == "vertica"
        assert spans[0].span_type == "sql"
        assert spans[0].name == "vertica.query"
        assert spans[0].get_metric("db.rowcount") == -1
        query = "INSERT INTO test_table (a, b) VALUES (1, 'aa');"
        assert spans[0].resource == query
        assert spans[0].get_tag("out.host") == "127.0.0.1"
        assert spans[0].get_tag("out.port") == "5433"
        assert spans[0].get_tag("db.name") == "docker"
        assert spans[0].get_tag("db.user") == "dbadmin"

        assert spans[1].resource == "SELECT * FROM test_table;"

    def test_cursor_override(self, test_conn):
        """Test overriding the tracer with our own."""
        conn, cur = test_conn

        test_tracer = get_dummy_tracer()
        Pin.override(cur, tracer=test_tracer)

        with conn:
            cur.execute("INSERT INTO {} (a, b) VALUES (1, 'aa');".format(TEST_TABLE))
            cur.execute("SELECT * FROM {};".format(TEST_TABLE))

        spans = test_tracer.writer.pop()
        assert len(spans) == 2

        # check all the metadata
        assert spans[0].service == "vertica"
        assert spans[0].span_type == "sql"
        assert spans[0].name == "vertica.query"
        assert spans[0].get_metric("db.rowcount") == -1
        query = "INSERT INTO test_table (a, b) VALUES (1, 'aa');"
        assert spans[0].resource == query
        assert spans[0].get_tag("out.host") == "127.0.0.1"
        assert spans[0].get_tag("out.port") == "5433"

        assert spans[1].resource == "SELECT * FROM test_table;"

    def test_execute_exception(self, test_conn, test_tracer):
        """Exceptions should result in appropriate span tagging."""
        from vertica_python.errors import VerticaSyntaxError

        conn, cur = test_conn

        with conn, pytest.raises(VerticaSyntaxError):
            cur.execute("INVALID QUERY")

        spans = test_tracer.writer.pop()
        assert len(spans) == 2

        # check all the metadata
        assert spans[0].service == "vertica"
        assert spans[0].error == 1
        assert "INVALID QUERY" in spans[0].get_tag(errors.ERROR_MSG)
        error_type = "vertica_python.errors.VerticaSyntaxError"
        assert spans[0].get_tag(errors.ERROR_TYPE) == error_type
        assert spans[0].get_tag(errors.ERROR_STACK)

        assert spans[1].resource == "COMMIT;"

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
                """.format(
                    TEST_TABLE
                )
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
        assert spans[0].name == "vertica.query"
        assert spans[1].get_metric("db.rowcount") == -1
        assert spans[1].name == "vertica.query"
        assert spans[1].get_metric("db.rowcount") == -1
        assert spans[2].name == "vertica.fetchone"
        assert spans[2].get_tag("out.host") == "127.0.0.1"
        assert spans[2].get_tag("out.port") == "5433"
        assert spans[2].get_metric("db.rowcount") == 1
        assert spans[3].name == "vertica.fetchone"
        assert spans[3].get_metric("db.rowcount") == 2
        assert spans[4].name == "vertica.fetchall"
        assert spans[4].get_metric("db.rowcount") == 5

    def test_nextset(self, test_conn, test_tracer):
        """cursor.nextset() should be traced."""
        conn, cur = test_conn

        with conn:
            cur.execute("SELECT * FROM {0}; SELECT * FROM {0}".format(TEST_TABLE))
            cur.nextset()

        spans = test_tracer.writer.pop()
        assert len(spans) == 3

        # check all the rowcounts
        assert spans[0].name == "vertica.query"
        assert spans[1].get_metric("db.rowcount") == -1
        assert spans[1].name == "vertica.nextset"
        assert spans[1].get_metric("db.rowcount") == -1
        assert spans[2].name == "vertica.query"
        assert spans[2].resource == "COMMIT;"

    def test_copy(self, test_conn, test_tracer):
        """cursor.copy() should be traced."""
        conn, cur = test_conn

        with conn:
            cur.copy(
                "COPY {0} (a, b) FROM STDIN DELIMITER ','".format(TEST_TABLE),
                "1,foo\n2,bar",
            )

        spans = test_tracer.writer.pop()
        assert len(spans) == 2

        # check all the rowcounts
        assert spans[0].name == "vertica.copy"
        query = "COPY test_table (a, b) FROM STDIN DELIMITER ','"
        assert spans[0].resource == query
        assert spans[1].name == "vertica.query"
        assert spans[1].resource == "COMMIT;"

    def test_opentracing(self, test_conn, test_tracer):
        """Ensure OpenTracing works with vertica."""
        conn, cur = test_conn

        ot_tracer = init_tracer("vertica_svc", test_tracer)

        with ot_tracer.start_active_span("vertica_execute"):
            cur.execute("INSERT INTO {} (a, b) VALUES (1, 'aa');".format(TEST_TABLE))
            conn.close()

        spans = test_tracer.writer.pop()
        assert len(spans) == 2
        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert dd_span.service == "vertica"
        assert dd_span.span_type == "sql"
        assert dd_span.name == "vertica.query"
        assert dd_span.get_metric("db.rowcount") == -1
        query = "INSERT INTO test_table (a, b) VALUES (1, 'aa');"
        assert dd_span.resource == query
        assert dd_span.get_tag("out.host") == "127.0.0.1"
        assert dd_span.get_tag("out.port") == "5433"
