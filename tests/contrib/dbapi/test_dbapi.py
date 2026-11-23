import mock
import pytest

from ddtrace.contrib._events.dbapi import DatabaseOperationEvent
from ddtrace.contrib._events.dbapi import DatabaseQueryPrepareEvent
from ddtrace.contrib._events.dbapi import DatabaseResultEvent
from ddtrace.contrib.dbapi import FetchTracedCursor
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.dbapi import TracedCursor
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.span_bus import find_span_on_context
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.propagation._database_monitoring import handle_query_prepare_event
from ddtrace.trace import Span
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

        traced_cursor = TracedCursor(cursor, {})
        # DEV: We always pass through the result
        assert "__result__" == traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")
        cursor.execute.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    def test_query_is_blocked_before_execution(self):
        for method in ("execute", "executemany"):
            with mock.patch.object(core, "dispatch_event", side_effect=BlockingException):
                with pytest.raises(BlockingException):
                    getattr(TracedCursor(self.cursor, cfg={}), method)("SELECT 1")

            getattr(self.cursor, method).assert_not_called()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    def test_dbm_propagation_not_supported(self):
        cursor = self.cursor
        cfg = IntegrationConfig(Config(), "dbapi", service="dbapi_service")
        # By default _dbm_propagator attribute should not be set or have a value of None.
        # DBM context propagation should be opt in.
        assert getattr(cfg, "_dbm_propagator", None) is None
        traced_cursor = TracedCursor(cursor, cfg=cfg)
        # Ensure dbm comment is not appended to sql statement
        traced_cursor.execute("SELECT * FROM db;")
        cursor.execute.assert_called_once_with("SELECT * FROM db;")

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    def test_cursor_execute_with_dbm_injection(self):
        cursor = self.cursor
        cfg = IntegrationConfig(Config(), "dbapi", service="orders-db", _dbm_propagator=_DBM_Propagator(0, "query"))
        traced_cursor = TracedCursor(cursor, cfg=cfg)

        # The following operations should generate DBM comments
        traced_cursor.execute("SELECT * FROM db;")
        traced_cursor.executemany("SELECT * FROM db;", ())
        traced_cursor.callproc("procedure_named_moon")

        spans = self.pop_spans()
        assert len(spans) == 3
        assert [span.resource for span in spans] == ["SELECT * FROM db;", "SELECT * FROM db;", "procedure_named_moon"]
        dbm_comment = "/*dddbs='orders-db',dde='staging',ddps='orders-app',ddpv='v7343437-d7ac743'*/ "
        cursor.execute.assert_called_once_with(dbm_comment + "SELECT * FROM db;")
        cursor.executemany.assert_called_once_with(dbm_comment + "SELECT * FROM db;", ())
        # DBM comment should not be added procedure names
        cursor.callproc.assert_called_once_with("procedure_named_moon")

    def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.executemany.return_value = "__result__"

        traced_cursor = TracedCursor(cursor, {})
        # DEV: We always pass through the result
        assert "__result__" == traced_cursor.executemany("__query__", "arg_1", kwarg1="kwarg1")
        cursor.executemany.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = "__result__"
        traced_cursor = TracedCursor(cursor, {})
        assert "__result__" == traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        cursor.fetchone.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = "__result__"
        traced_cursor = TracedCursor(cursor, {})
        assert "__result__" == traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        cursor.fetchall.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchmany.return_value = "__result__"
        traced_cursor = TracedCursor(cursor, {})
        assert "__result__" == traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        cursor.fetchmany.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_correct_span_names(self):
        cursor = self.cursor
        cursor.rowcount = 0
        traced_cursor = TracedCursor(cursor, {})

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

    def test_span_info(self):
        cursor = self.cursor
        cursor.rowcount = 123
        cfg = IntegrationConfig(Config(), "db-test", service="my_service")
        traced_cursor = TracedCursor(cursor, cfg=cfg)

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        # Only measure if the name passed matches the default name (e.g. `sql.query` and not `sql.query.fetchall`)
        assert_is_not_measured(span)
        assert span.get_tag("extra1") == "value_extra1", "Extra tags are preserved"
        assert span.name == "my_name", "Span name is respected"
        assert span.service == "my_service", "Span service is respected"
        assert span.resource == "my_resource", "Resource is respected"
        assert span.span_type == "sql", "Span has the correct span type"
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"
        assert span.get_tag("component") == traced_cursor._self_config.integration_name
        assert span.get_tag("span.kind") == "client"

    def test_cfg_service(self):
        cursor = self.cursor
        cursor.rowcount = 123
        cfg = IntegrationConfig(Config(), "db-test", service="cfg-service")
        traced_cursor = TracedCursor(cursor, cfg=cfg)

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.service == "cfg-service"

    def test_default_service(self):
        cursor = self.cursor
        cursor.rowcount = 123
        traced_cursor = TracedCursor(cursor, cfg={})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.service == "db"

    def test_default_service_cfg(self):
        cursor = self.cursor
        cursor.rowcount = 123
        cfg = IntegrationConfig(Config(), "db-test", _default_service="default-svc")
        traced_cursor = TracedCursor(cursor, cfg=cfg)

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.service == "default-svc"

    def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        # Django integration used to have its own TracedCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        cfg = IntegrationConfig(Config(), "db-test")
        traced_cursor = TracedCursor(cursor, cfg=cfg)

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"


class TestFetchTracedCursor(TracerTestCase):
    def setUp(self):
        super(TestFetchTracedCursor, self).setUp()
        self.cursor = mock.Mock()
        self.config = IntegrationConfig(Config(), "db-test", _default_service="default-svc")

    def test_execute_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"

        traced_cursor = FetchTracedCursor(cursor, cfg={})
        assert "__result__" == traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")
        cursor.execute.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.executemany.return_value = "__result__"

        traced_cursor = FetchTracedCursor(cursor, cfg={})
        assert "__result__" == traced_cursor.executemany("__query__", "arg_1", kwarg1="kwarg1")
        cursor.executemany.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = "__result__"
        traced_cursor = FetchTracedCursor(cursor, cfg={})
        assert "__result__" == traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        cursor.fetchone.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = "__result__"
        traced_cursor = FetchTracedCursor(cursor, {})
        assert "__result__" == traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        cursor.fetchall.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchmany.return_value = "__result__"
        traced_cursor = FetchTracedCursor(cursor, cfg={})
        assert "__result__" == traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        cursor.fetchmany.assert_called_once_with("arg_1", kwarg1="kwarg1")

    def test_correct_span_names(self):
        cursor = self.cursor
        cursor.rowcount = 0
        traced_cursor = FetchTracedCursor(cursor, cfg={})

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

    def test_span_info(self):
        cursor = self.cursor
        cursor.rowcount = 123
        cfg = IntegrationConfig(Config(), "db-test", service="my_service")
        traced_cursor = FetchTracedCursor(cursor, cfg=cfg, db_tags={"tag": "value_tag"})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.get_tag("extra1") == "value_extra1", "Extra tags are preserved"
        assert span.name == "my_name", "Span name is respected"
        assert span.service == "my_service", "Span service is respected"
        assert span.resource == "my_resource", "Resource is respected"
        assert span.span_type == "sql", "Span has the correct span type"
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"
        assert span.get_tag("component") == traced_cursor._self_config.integration_name
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("tag") == "value_tag"

    def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        # Django integration used to have its own FetchTracedCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        traced_cursor = FetchTracedCursor(cursor, cfg={})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"

    def test_unknown_rowcount(self):
        class Unknown(object):
            pass

        cursor = self.cursor
        cursor.rowcount = Unknown()
        traced_cursor = FetchTracedCursor(cursor, cfg={})

        def method():
            pass

        traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.get_metric("db.row_count") is None

    def test_callproc_can_handle_arbitrary_args(self):
        cursor = self.cursor
        cursor.callproc.return_value = "gme --> moon"
        traced_cursor = TracedCursor(cursor, cfg={})

        traced_cursor.callproc("proc_name", "arg_1")
        spans = self.pop_spans()
        assert len(spans) == 1
        self.reset()

        traced_cursor.callproc("proc_name", "arg_1", "arg_2")
        spans = self.pop_spans()
        assert len(spans) == 1
        self.reset()

        traced_cursor.callproc("proc_name", "arg_1", "arg_2", {"arg_key": "arg_value"})
        spans = self.pop_spans()
        assert len(spans) == 1
        self.reset()

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="full",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    def test_cursor_execute_fetch_with_dbm_injection(self):
        cursor = self.cursor
        dbm_propagator = _DBM_Propagator(0, "query")
        cfg = IntegrationConfig(Config(), "dbapi", service="dbapi_service", _dbm_propagator=dbm_propagator)
        traced_cursor = FetchTracedCursor(cursor, cfg=cfg)

        # The following operations should not generate DBM comments
        traced_cursor.fetchone()
        traced_cursor.fetchall()
        traced_cursor.fetchmany(1)
        traced_cursor.callproc("proc")
        cursor.fetchone.assert_called_once_with()
        cursor.fetchall.assert_called_once_with()
        cursor.fetchmany.assert_called_once_with(1)
        cursor.callproc.assert_called_once_with("proc")

        spans = self.pop_spans()
        assert len(spans) == 4
        assert all(span.get_tag("_dd.dbm_trace_injected") is None for span in spans)

        # The following operations should generate DBM comments
        traced_cursor.execute("SELECT * FROM db;")
        traced_cursor.executemany("SELECT * FROM db;", ())

        spans = self.pop_spans()
        assert len(spans) == 2
        assert [span.resource for span in spans] == ["SELECT * FROM db;", "SELECT * FROM db;"]
        assert all(span.get_tag("_dd.dbm_trace_injected") == "true" for span in spans)
        dbm_comment_exc = dbm_propagator._get_dbm_comment(spans[0])
        cursor.execute.assert_called_once_with(dbm_comment_exc + "SELECT * FROM db;")
        dbm_comment_excmany = dbm_propagator._get_dbm_comment(spans[1])
        cursor.executemany.assert_called_once_with(dbm_comment_excmany + "SELECT * FROM db;", ())


class TestTracedConnection(TracerTestCase):
    def setUp(self):
        super(TestTracedConnection, self).setUp()
        self.connection = mock.Mock()

    def test_cursor_class(self):
        # Default
        traced_connection = TracedConnection(self.connection)
        self.assertTrue(traced_connection._self_cursor_cls is TracedCursor)

        # Trace fetched methods
        with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
            traced_connection = TracedConnection(self.connection)
            self.assertTrue(traced_connection._self_cursor_cls is FetchTracedCursor)

        # Manually provided cursor class
        with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
            traced_connection = TracedConnection(self.connection, cursor_cls=TracedCursor)
            self.assertTrue(traced_connection._self_cursor_cls is TracedCursor)

    def test_commit_is_traced(self):
        connection = self.connection
        connection.commit.return_value = None
        traced_connection = TracedConnection(connection)
        traced_connection.commit()
        assert self.pop_spans()[0].name == "mock.connection.commit"
        connection.commit.assert_called_with()

    def test_rollback_is_traced(self):
        connection = self.connection
        connection.rollback.return_value = None
        traced_connection = TracedConnection(connection)
        traced_connection.rollback()
        assert self.pop_spans()[0].name == "mock.connection.rollback"
        connection.rollback.assert_called_with()

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

        conn = TracedConnection(ConnectionConnection())
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

        with TracedConnection(ConnectionCursor()) as cursor:
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
                return TracedCursor(Cursor(), {})

            def commit(self):
                pass

        with TracedConnection(ConnectionTracedCursor()) as cursor:
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

        conn = TracedConnection(ConnectionDifferentConnection())
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

        conn = TracedConnection(ConnectionDifferentConnection())
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

        conn = TracedConnection(ConnectionNoCtx())
        with pytest.raises(AttributeError):
            with conn:
                pass

        with pytest.raises(AttributeError):
            with conn as conn2:
                pass

        spans = self.pop_spans()
        assert len(spans) == 0


@pytest.mark.parametrize("method_name", ["execute", "executemany", "callproc", "fetchone"])
@pytest.mark.parametrize("fails", [False, True])
def test_post_execute_hook_receives_active_span(tracer, test_spans, method_name, fails):
    hook_spans = []

    class ExtendedCursor(FetchTracedCursor):
        def _set_post_execute_tags(self, span: Span) -> None:
            super()._set_post_execute_tags(span)
            assert span is tracer.current_span()
            assert not span.finished
            span.set_tag("driver.query_id", self.__wrapped__.query_id)
            hook_spans.append(span)

    result = object()
    failure = ValueError("driver execution failed")
    cursor = mock.Mock(rowcount=7, query_id="query-123")
    method = getattr(cursor, method_name)
    method.return_value = result
    if fails:
        method.side_effect = failure
    traced = ExtendedCursor(cursor)
    query = "SELECT 1"
    traced._self_last_execute_operation = query
    args = () if method_name == "fetchone" else (query,)
    execution_context = core.current

    with tracer.trace("parent") as parent:
        if fails:
            with pytest.raises(ValueError) as caught:
                getattr(traced, method_name)(*args)
            assert caught.value is failure
        else:
            assert getattr(traced, method_name)(*args) is result
        assert tracer.current_span() is parent
        assert core.current is execution_context
        assert len(hook_spans) == 1
        span = hook_spans[0]
        assert span.finished
        assert span.parent_id == parent.span_id
        assert span.resource == query
        assert span.error == int(fails)
        assert span.get_metric("db.row_count") == 7
        assert span.get_tag("driver.query_id") == "query-123"
        if fails:
            assert span.get_tag("error.message") == str(failure)

    assert test_spans.spans == [parent, span]


@pytest.mark.parametrize("method_name", ["commit", "rollback"])
def test_transaction_failure_restores_parent(tracer, test_spans, method_name):
    failure = ValueError("transaction failed")
    connection = mock.Mock()
    getattr(connection, method_name).side_effect = failure
    traced = TracedConnection(connection)
    execution_context = core.current

    with tracer.trace("parent") as parent:
        with pytest.raises(ValueError) as caught:
            getattr(traced, method_name)()
        assert caught.value is failure
        assert tracer.current_span() is parent
        assert core.current is execution_context

    assert len(test_spans.spans) == 2
    span = test_spans.spans[1]
    assert span.name.endswith("." + method_name)
    assert span.parent_id == parent.span_id
    assert span.finished
    assert span.error == 1
    assert span.span_type is None
    assert_is_not_measured(span)


def test_disabled_tracing_calls_driver_without_database_context(tracer, test_spans):
    result = object()
    cursor = mock.Mock(rowcount=1)
    cursor.execute.return_value = result
    with mock.patch.object(tracer, "enabled", False):
        with mock.patch.object(core, "context_with_event", wraps=core.context_with_event) as context:
            assert TracedCursor(cursor).execute("SELECT 1") is result
    cursor.execute.assert_called_once_with("SELECT 1")
    context.assert_not_called()
    assert test_spans.spans == []


@pytest.mark.parametrize("method_name", ["execute", "executemany", "callproc", "fetchone"])
def test_query_preparation_is_product_neutral(tracer, test_spans, method_name):
    prepared = []
    parameters = object()
    cursor = mock.Mock(rowcount=1)
    cfg = IntegrationConfig(Config(), "db-test", service="orders-db")
    traced = FetchTracedCursor(cursor, cfg=cfg)

    def prepare(event):
        span = find_span_on_context(event.context)
        assert span is tracer.current_span()
        assert span.service == "orders-db"
        assert span.resource == "SELECT 1"
        prepared.append(event.context)
        event.args = ("/* prepared */ " + event.args[0], *event.args[1:])

    core.on(DatabaseQueryPrepareEvent.event_name, prepare)
    try:
        args = ("SELECT 1", parameters) if method_name != "fetchone" else ()
        getattr(traced, method_name)(*args)
    finally:
        core.reset_listeners(DatabaseQueryPrepareEvent.event_name, prepare)

    if method_name in ("execute", "executemany"):
        assert len(prepared) == 1
        getattr(cursor, method_name).assert_called_once_with("/* prepared */ SELECT 1", parameters)
    else:
        assert prepared == []
        getattr(cursor, method_name).assert_called_once_with(*args)
    assert len(test_spans.spans) == 1
    if method_name != "fetchone":
        assert test_spans.spans[0].resource == "SELECT 1"


@pytest.mark.parametrize("fails", [False, True])
def test_result_metadata_hook_needs_no_span(tracer, test_spans, fails):
    class MetadataCursor(TracedCursor):
        def _get_post_execute_result(self, context):
            result = super()._get_post_execute_result(context)
            result.tags["driver.query_id"] = "query-123"
            return result

        def _set_post_execute_tags(self, span):
            super()._set_post_execute_tags(span)
            span.set_tag("legacy.query_id", "legacy-123")

    cursor = mock.Mock(rowcount=7)
    if fails:
        cursor.execute.side_effect = ValueError("driver failed")
        with pytest.raises(ValueError, match="driver failed"):
            MetadataCursor(cursor).execute("SELECT 1")
    else:
        MetadataCursor(cursor).execute("SELECT 1")

    (span,) = test_spans.spans
    assert span.get_metric("db.row_count") == 7
    assert span.get_tag("driver.query_id") == "query-123"
    assert span.get_tag("legacy.query_id") == "legacy-123"


def test_missing_operation_span_does_not_borrow_parent(tracer, test_spans):
    propagator = mock.Mock()
    cfg = IntegrationConfig(Config(), "db-test", _dbm_propagator=propagator)
    operation = DatabaseOperationEvent(component="db-test", integration_config=cfg, operation_name="sql.query")

    with tracer.trace("parent") as parent:
        with core.context_with_data("unassociated", event=operation) as context:
            args = ("SELECT 1",)
            prepare = DatabaseQueryPrepareEvent(context=context, args=args, kwargs={})
            handle_query_prepare_event(prepare)
            core.dispatch_event(DatabaseResultEvent(context=context, row_count=99, tags={"wrong.target": "yes"}))
            assert find_span_on_context(context) is None
            assert prepare.args is args
            assert tracer.current_span() is parent
        assert parent.get_metric("db.row_count") is None
        assert parent.get_tag("wrong.target") is None

    propagator.inject.assert_not_called()
    assert len(test_spans.spans) == 1


def test_legacy_hook_failure_reaches_application(tracer, test_spans):
    failure = ValueError("legacy hook failed")

    class LegacyCursor(TracedCursor):
        def _set_post_execute_tags(self, span):
            assert span is tracer.current_span()
            raise failure

    cursor = mock.Mock()
    type(cursor).rowcount = mock.PropertyMock(side_effect=RuntimeError("cursor closed"))
    try:
        raise LookupError("already handled by the application")
    except LookupError:
        with tracer.trace("parent") as parent:
            with pytest.raises(ValueError) as caught:
                LegacyCursor(cursor).execute("SELECT 1")
            assert caught.value is failure
            assert tracer.current_span() is parent

    assert len(test_spans.spans) == 2
    span = test_spans.spans[1]
    assert span.finished
    assert span.error == 1
    assert span.get_tag("error.message") == str(failure)


def test_monkeypatched_legacy_hook_receives_real_span(tracer, test_spans):
    original = TracedCursor._set_post_execute_tags
    observed = []

    def legacy_hook(cursor, span):
        assert span is tracer.current_span()
        observed.append(span)
        original(cursor, span)

    with mock.patch.object(TracedCursor, "_set_post_execute_tags", autospec=True, side_effect=legacy_hook) as hook:
        TracedCursor(mock.Mock(rowcount=7)).execute("SELECT 1")
    hook.assert_called_once()
    assert len(test_spans.spans) == 1
    assert observed == test_spans.get_spans()
    assert observed[0].get_metric("db.row_count") == 7


@pytest.mark.parametrize("keyword", [False, True])
def test_legacy_trace_method_contract(tracer, keyword):
    class LegacyCursor(TracedCursor):
        def _trace_method(self, method, name, resource, extra_tags, dbm_propagator, *args, **kwargs):
            assert dbm_propagator is self._self_dbm_propagator
            if keyword:
                return super()._trace_method(
                    method, name, resource, extra_tags, dbm_propagator=self._self_dbm_propagator, query=args[0]
                )
            return super()._trace_method(method, name, resource, extra_tags, self._self_dbm_propagator, *args, **kwargs)

    cursor = mock.Mock(spec=["execute", "rowcount"], rowcount=1)
    cfg = IntegrationConfig(Config(), "db-test", _dbm_propagator=object())
    LegacyCursor(cursor, cfg=cfg).execute("SELECT 1")
    if keyword:
        cursor.execute.assert_called_once_with(query="SELECT 1")
    else:
        cursor.execute.assert_called_once_with("SELECT 1")


@pytest.mark.parametrize("cleanup", ["hook", "rowcount"])
def test_cleanup_failure_preserves_driver_error(tracer, test_spans, cleanup):
    class LegacyCursor(TracedCursor):
        def _set_post_execute_tags(self, span):
            raise RuntimeError("hook failed")

    failure = ValueError("driver failed")
    cursor = mock.Mock(rowcount=1)
    cursor.execute.side_effect = failure
    if cleanup == "rowcount":
        type(cursor).rowcount = mock.PropertyMock(side_effect=RuntimeError("cursor closed"))
    traced = (LegacyCursor if cleanup == "hook" else TracedCursor)(cursor)
    with tracer.trace("parent") as parent:
        with pytest.raises(ValueError) as caught:
            traced.execute("SELECT 1")
        assert caught.value is failure
        assert tracer.current_span() is parent
    assert test_spans.spans[1].get_tag("error.message") == "driver failed"


@pytest.mark.parametrize("suppression", ["argument", "cursor"])
def test_legacy_propagator_suppression(tracer, suppression):
    cursor = mock.Mock(rowcount=1)
    traced = TracedCursor(cursor, cfg=IntegrationConfig(Config(), "db-test", _dbm_propagator=object()))
    prepare = mock.Mock()
    core.on(DatabaseQueryPrepareEvent.event_name, prepare)
    try:
        if suppression == "cursor":
            traced._self_dbm_propagator = None
            traced.execute("SELECT 1")
        else:
            traced._trace_method(cursor.execute, "sql.query", "SELECT 1", {}, None, "SELECT 1")
    finally:
        core.reset_listeners(DatabaseQueryPrepareEvent.event_name, prepare)
    prepare.assert_not_called()
    cursor.execute.assert_called_once_with("SELECT 1")
