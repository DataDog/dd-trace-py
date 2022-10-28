import mock
import pytest

from ddtrace import Pin
from ddtrace.constants import ANALYTICS_SAMPLE_RATE_KEY
from ddtrace.contrib.dbapi import FetchTracedCursor
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.settings import Config
from ddtrace.settings import _database_monitoring
from ddtrace.settings.integration import IntegrationConfig
from ddtrace.span import Span
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_is_not_measured


class TestTracedCursor(TracerTestCase):
    def setUp(self):
        super(TestTracedCursor, self).setUp()
        self.cursor = mock.Mock()

    def test_execute_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"

        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin, {})
        # DEV: We always pass through the result
        assert "__result__" == traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")
        cursor.execute.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="full",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    def test_curser_execute_with_dbm_injection(self):
        cursor = self.cursor
        traced_cursor = TracedCursor(cursor, Pin("pin_name", tracer=self.tracer), {})
        query = "SELECT * FROM db;"
        traced_cursor.execute(query)
        spans = self.tracer.pop()
        assert len(spans) == 1
        dbm_comment = _database_monitoring._get_dbm_comment(spans[0])
        cursor.execute.assert_called_once_with(dbm_comment + query)

    def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.executemany.return_value = "__result__"

        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin, {})
        # DEV: We always pass through the result
        assert "__result__" == traced_cursor.executemany("__query__", "arg_1", kwarg1="kwarg1")
        cursor.executemany.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = "__result__"
        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin, {})
        assert "__result__" == traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        cursor.fetchone.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = "__result__"
        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin, {})
        assert "__result__" == traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        cursor.fetchall.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchmany.return_value = "__result__"
        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin, {})
        assert "__result__" == traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        cursor.fetchmany.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_correct_span_names(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        pin = Pin("pin_name", tracer=tracer)
        traced_cursor = TracedCursor(cursor, pin, {})

        traced_cursor.execute("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query"))
        assert_is_measured(self.get_root_span())
        self.reset()

        traced_cursor.executemany("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query"))
        assert_is_measured(self.get_root_span())
        self.reset()

        traced_cursor.callproc("arg_1", "arg2")
        self.assert_structure(dict(name="sql.query"))
        assert_is_measured(self.get_root_span())
        self.reset()

        traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        self.assert_has_no_spans()

        traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        self.assert_has_no_spans()

        traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        self.assert_has_no_spans()

    def test_when_pin_disabled_then_no_tracing(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"
        cursor.executemany.return_value = "__result__"

        tracer.enabled = False
        pin = Pin("pin_name", tracer=tracer)
        traced_cursor = TracedCursor(cursor, pin, {})

        assert "__result__" == traced_cursor.execute("arg_1", kwarg1="kwarg1")
        assert len(tracer.pop()) == 0

        assert "__result__" == traced_cursor.executemany("arg_1", kwarg1="kwarg1")
        assert len(tracer.pop()) == 0

        cursor.callproc.return_value = "callproc"
        assert "callproc" == traced_cursor.callproc("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchone.return_value = "fetchone"
        assert "fetchone" == traced_cursor.fetchone("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchmany.return_value = "fetchmany"
        assert "fetchmany" == traced_cursor.fetchmany("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchall.return_value = "fetchall"
        assert "fetchall" == traced_cursor.fetchall("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

    def test_span_info(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin("my_service", tracer=tracer, tags={"pin1": "value_pin1"})
        traced_cursor = TracedCursor(cursor, pin, {})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        # Only measure if the name passed matches the default name (e.g. `sql.query` and not `sql.query.fetchall`)
        assert_is_not_measured(span)
        assert span.get_tag("pin1") == "value_pin1", "Pin tags are preserved"
        assert span.get_tag("extra1") == "value_extra1", "Extra tags are merged into pin tags"
        assert span.name == "my_name", "Span name is respected"
        assert span.service == "my_service", "Service from pin"
        assert span.resource == "my_resource", "Resource is respected"
        assert span.span_type == "sql", "Span has the correct span type"
        # Row count
        assert span.get_metric("db.rowcount") == 123, "Row count is set as a metric"
        assert span.get_metric("sql.rows") == 123, "Row count is set as a tag (for legacy django cursor replacement)"

    def test_cfg_service(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin(None, tracer=tracer, tags={"pin1": "value_pin1"})
        cfg = IntegrationConfig(Config(), "db-test", service="cfg-service")
        traced_cursor = TracedCursor(cursor, pin, cfg)

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        assert span.service == "cfg-service"

    def test_default_service(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin(None, tracer=tracer, tags={"pin1": "value_pin1"})

        traced_cursor = TracedCursor(cursor, pin, {})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        assert span.service == "db"

    def test_default_service_cfg(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin(None, tracer=tracer, tags={"pin1": "value_pin1"})
        cfg = IntegrationConfig(Config(), "db-test", _default_service="default-svc")
        traced_cursor = TracedCursor(cursor, pin, cfg)

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        assert span.service == "default-svc"

    def test_service_cfg_and_pin(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin("pin-svc", tracer=tracer, tags={"pin1": "value_pin1"})
        cfg = IntegrationConfig(Config(), "db-test", _default_service="default-svc")
        traced_cursor = TracedCursor(cursor, pin, cfg)

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        assert span.service == "pin-svc"

    def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        tracer = self.tracer
        # Django integration used to have its own TracedCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        pin = Pin("my_service", tracer=tracer, tags={"pin1": "value_pin1"})
        cfg = IntegrationConfig(Config(), "db-test")
        traced_cursor = TracedCursor(cursor, pin, cfg)

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        # Row count
        assert span.get_metric("db.rowcount") == 123, "Row count is set as a metric"
        assert span.get_metric("sql.rows") == 123, "Row count is set as a tag (for legacy django cursor replacement)"

    def test_cursor_analytics_default(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"

        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = TracedCursor(cursor, pin, {})
        # DEV: We always pass through the result
        assert "__result__" == traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")

        span = self.pop_spans()[0]
        self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))


class TestFetchTracedCursor(TracerTestCase):
    def setUp(self):
        super(TestFetchTracedCursor, self).setUp()
        self.cursor = mock.Mock()
        self.config = IntegrationConfig(Config(), "db-test", _default_service="default-svc")

    def test_execute_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"

        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = FetchTracedCursor(cursor, pin, {})
        assert "__result__" == traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")
        cursor.execute.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.executemany.return_value = "__result__"

        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = FetchTracedCursor(cursor, pin, {})
        assert "__result__" == traced_cursor.executemany("__query__", "arg_1", kwarg1="kwarg1")
        cursor.executemany.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = "__result__"
        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = FetchTracedCursor(cursor, pin, {})
        assert "__result__" == traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        cursor.fetchone.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = "__result__"
        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = FetchTracedCursor(cursor, pin, {})
        assert "__result__" == traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        cursor.fetchall.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchmany.return_value = "__result__"
        pin = Pin("pin_name", tracer=self.tracer)
        traced_cursor = FetchTracedCursor(cursor, pin, {})
        assert "__result__" == traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        cursor.fetchmany.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_correct_span_names(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        pin = Pin("pin_name", tracer=tracer)
        traced_cursor = FetchTracedCursor(cursor, pin, {})

        traced_cursor.execute("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query"))
        self.reset()

        traced_cursor.executemany("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query"))
        self.reset()

        traced_cursor.callproc("arg_1", "arg2")
        self.assert_structure(dict(name="sql.query"))
        self.reset()

        traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query.fetchone"))
        self.reset()

        traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query.fetchmany"))
        self.reset()

        traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query.fetchall"))
        self.reset()

    def test_when_pin_disabled_then_no_tracing(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"
        cursor.executemany.return_value = "__result__"

        tracer.enabled = False
        pin = Pin("pin_name", tracer=tracer)
        traced_cursor = FetchTracedCursor(cursor, pin, {})

        assert "__result__" == traced_cursor.execute("arg_1", kwarg1="kwarg1")
        assert len(tracer.pop()) == 0

        assert "__result__" == traced_cursor.executemany("arg_1", kwarg1="kwarg1")
        assert len(tracer.pop()) == 0

        cursor.callproc.return_value = "callproc"
        assert "callproc" == traced_cursor.callproc("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchone.return_value = "fetchone"
        assert "fetchone" == traced_cursor.fetchone("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchmany.return_value = "fetchmany"
        assert "fetchmany" == traced_cursor.fetchmany("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchall.return_value = "fetchall"
        assert "fetchall" == traced_cursor.fetchall("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

    def test_span_info(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin("my_service", tracer=tracer, tags={"pin1": "value_pin1"})
        traced_cursor = FetchTracedCursor(cursor, pin, {})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        assert span.get_tag("pin1") == "value_pin1", "Pin tags are preserved"
        assert span.get_tag("extra1") == "value_extra1", "Extra tags are merged into pin tags"
        assert span.name == "my_name", "Span name is respected"
        assert span.service == "my_service", "Service from pin"
        assert span.resource == "my_resource", "Resource is respected"
        assert span.span_type == "sql", "Span has the correct span type"
        # Row count
        assert span.get_metric("db.rowcount") == 123, "Row count is set as a metric"
        assert span.get_metric("sql.rows") == 123, "Row count is set as a tag (for legacy django cursor replacement)"

    def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        tracer = self.tracer
        # Django integration used to have its own FetchTracedCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        pin = Pin("my_service", tracer=tracer, tags={"pin1": "value_pin1"})
        traced_cursor = FetchTracedCursor(cursor, pin, {})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        # Row count
        assert span.get_metric("db.rowcount") == 123, "Row count is set as a metric"
        assert span.get_metric("sql.rows") == 123, "Row count is set as a tag (for legacy django cursor replacement)"

    def test_fetch_no_analytics(self):
        """Confirm fetch* methods do not have analytics sample rate metric"""
        with self.override_config("dbapi2", dict(analytics_enabled=True)):
            cursor = self.cursor
            cursor.rowcount = 0
            cursor.fetchone.return_value = "__result__"
            pin = Pin("pin_name", tracer=self.tracer)
            traced_cursor = FetchTracedCursor(cursor, pin, {})
            assert "__result__" == traced_cursor.fetchone("arg_1", kwarg1="kwarg1")

            span = self.pop_spans()[0]
            self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

            cursor = self.cursor
            cursor.rowcount = 0
            cursor.fetchall.return_value = "__result__"
            pin = Pin("pin_name", tracer=self.tracer)
            traced_cursor = FetchTracedCursor(cursor, pin, {})
            assert "__result__" == traced_cursor.fetchall("arg_1", kwarg1="kwarg1")

            span = self.pop_spans()[0]
            self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

            cursor = self.cursor
            cursor.rowcount = 0
            cursor.fetchmany.return_value = "__result__"
            pin = Pin("pin_name", tracer=self.tracer)
            traced_cursor = FetchTracedCursor(cursor, pin, {})
            assert "__result__" == traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")

            span = self.pop_spans()[0]
            self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_unknown_rowcount(self):
        class Unknown(object):
            pass

        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = Unknown()
        pin = Pin("my_service", tracer=tracer, tags={"pin1": "value_pin1"})
        traced_cursor = FetchTracedCursor(cursor, pin, {})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"})
        span = tracer.pop()[0]  # type: Span
        assert span.get_metric("db.rowcount") is None
        assert span.get_metric("sql.rows") is None

    def test_callproc_can_handle_arbitrary_args(self):
        cursor = self.cursor
        tracer = self.tracer
        pin = Pin("pin_name", tracer=tracer)
        cursor.callproc.return_value = "gme --> moon"
        traced_cursor = TracedCursor(cursor, pin, {})

        traced_cursor.callproc("proc_name", "arg_1")
        spans = self.tracer.pop()
        assert len(spans) == 1
        self.reset()

        traced_cursor.callproc("proc_name", "arg_1", "arg_2")
        spans = self.tracer.pop()
        assert len(spans) == 1
        self.reset()

        traced_cursor.callproc("proc_name", "arg_1", "arg_2", {"arg_key": "arg_value"})
        spans = self.tracer.pop()
        assert len(spans) == 1
        self.reset()


class TestTracedConnection(TracerTestCase):
    def setUp(self):
        super(TestTracedConnection, self).setUp()
        self.connection = mock.Mock()

    def test_cursor_class(self):
        pin = Pin("pin_name", tracer=self.tracer)

        # Default
        traced_connection = TracedConnection(self.connection, pin=pin)
        self.assertTrue(traced_connection._self_cursor_cls is TracedCursor)

        # Trace fetched methods
        with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
            traced_connection = TracedConnection(self.connection, pin=pin)
            self.assertTrue(traced_connection._self_cursor_cls is FetchTracedCursor)

        # Manually provided cursor class
        with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
            traced_connection = TracedConnection(self.connection, pin=pin, cursor_cls=TracedCursor)
            self.assertTrue(traced_connection._self_cursor_cls is TracedCursor)

    def test_commit_is_traced(self):
        connection = self.connection
        tracer = self.tracer
        connection.commit.return_value = None
        pin = Pin("pin_name", tracer=tracer)
        traced_connection = TracedConnection(connection, pin)
        traced_connection.commit()
        assert tracer.pop()[0].name == "mock.connection.commit"
        connection.commit.assert_called_with()

    def test_rollback_is_traced(self):
        connection = self.connection
        tracer = self.tracer
        connection.rollback.return_value = None
        pin = Pin("pin_name", tracer=tracer)
        traced_connection = TracedConnection(connection, pin)
        traced_connection.rollback()
        assert tracer.pop()[0].name == "mock.connection.rollback"
        connection.rollback.assert_called_with()

    def test_connection_analytics_with_rate(self):
        with self.override_config("dbapi2", dict(analytics_enabled=True, analytics_sample_rate=0.5)):
            connection = self.connection
            tracer = self.tracer
            connection.commit.return_value = None
            pin = Pin("pin_name", tracer=tracer)
            traced_connection = TracedConnection(connection, pin)
            traced_connection.commit()
            span = tracer.pop()[0]
            self.assertIsNone(span.get_metric(ANALYTICS_SAMPLE_RATE_KEY))

    def test_connection_context_manager(self):
        class Cursor(object):
            rowcount = 0

            def execute(self, *args, **kwargs):
                pass

            def fetchall(self, *args, **kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def commit(self, *args, **kwargs):
                pass

        # When a connection is returned from a context manager the object proxy
        # should be returned so that tracing works.

        class ConnectionConnection(object):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                pass

        pin = Pin("pin", tracer=self.tracer)
        conn = TracedConnection(ConnectionConnection(), pin)
        with conn as conn2:
            conn2.commit()
        spans = self.pop_spans()
        assert len(spans) == 1

        with conn as conn2:
            with conn2.cursor() as cursor:
                cursor.execute("query")
                cursor.fetchall()

        spans = self.pop_spans()
        assert len(spans) == 1

        # If a cursor is returned from the context manager
        # then it should be instrumented.

        class ConnectionCursor(object):
            def __enter__(self):
                return Cursor()

            def __exit__(self, *exc):
                return False

            def commit(self):
                pass

        with TracedConnection(ConnectionCursor(), pin) as cursor:
            cursor.execute("query")
            cursor.fetchall()
        spans = self.pop_spans()
        assert len(spans) == 1

        # If a traced cursor is returned then it should not
        # be double instrumented.

        class ConnectionTracedCursor(object):
            def __enter__(self):
                return self.cursor()

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return TracedCursor(Cursor(), pin, {})

            def commit(self):
                pass

        with TracedConnection(ConnectionTracedCursor(), pin) as cursor:
            cursor.execute("query")
            cursor.fetchall()
        spans = self.pop_spans()
        assert len(spans) == 1

        # Check when a different connection object is returned
        # from a connection context manager.
        # No traces should be produced.

        other_conn = ConnectionConnection()

        class ConnectionDifferentConnection(object):
            def __enter__(self):
                return other_conn

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                pass

        conn = TracedConnection(ConnectionDifferentConnection(), pin)
        with conn as conn2:
            conn2.commit()
        spans = self.pop_spans()
        assert len(spans) == 0

        with conn as conn2:
            with conn2.cursor() as cursor:
                cursor.execute("query")
                cursor.fetchall()

        spans = self.pop_spans()
        assert len(spans) == 0

        # When some unexpected value is returned from the context manager
        # it should be handled gracefully.

        class ConnectionUnknown(object):
            def __enter__(self):
                return 123456

            def __exit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

            def commit(self):
                pass

        conn = TracedConnection(ConnectionDifferentConnection(), pin)
        with conn as conn2:
            conn2.commit()
        spans = self.pop_spans()
        assert len(spans) == 0

        with conn as conn2:
            with conn2.cursor() as cursor:
                cursor.execute("query")
                cursor.fetchall()

        spans = self.pop_spans()
        assert len(spans) == 0

        # Errors should be the same when no context management is defined.

        class ConnectionNoCtx(object):
            def cursor(self):
                return Cursor()

            def commit(self):
                pass

        conn = TracedConnection(ConnectionNoCtx(), pin)
        with pytest.raises(AttributeError):
            with conn:
                pass

        with pytest.raises(AttributeError):
            with conn as conn2:
                pass

        spans = self.pop_spans()
        assert len(spans) == 0
