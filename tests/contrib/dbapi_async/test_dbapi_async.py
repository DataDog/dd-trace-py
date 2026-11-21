import asyncio

import mock
import pytest

from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.contrib.dbapi_async import FetchTracedAsyncCursor
from ddtrace.contrib.dbapi_async import TracedAsyncConnection
from ddtrace.contrib.dbapi_async import TracedAsyncCursor
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.trace import Span
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.utils import assert_is_measured
from tests.utils import assert_is_not_measured


class TestTracedAsyncCursor(AsyncioTestCase):
    def setUp(self):
        super(TestTracedAsyncCursor, self).setUp()
        self.cursor = mock.AsyncMock()

    async def test_execute_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"

        traced_cursor = TracedAsyncCursor(cursor, cfg={})
        # DEV: We always pass through the result
        assert "__result__" == await traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")
        cursor.execute.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    async def test_query_is_blocked_before_execution(self):
        for query in ("SELECT 1", b"SELECT 1"):
            for method in ("execute", "executemany"):
                cursor = TracedAsyncCursor(self.cursor, cfg={"_dbapi_span_name_prefix": "postgres"})
                with mock.patch.object(core, "dispatch_event", side_effect=BlockingException) as dispatch_event:
                    with pytest.raises(BlockingException):
                        await getattr(cursor, method)(query)

                dispatch_event.assert_called_once_with(
                    DbQueryEvent(query=query, span_name_prefix="postgres", get_encoding=cursor._dbapi_query_encoding)
                )
                getattr(self.cursor, method).assert_not_awaited()

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    async def test_dbm_propagation_not_supported(self):
        cursor = self.cursor
        cfg = IntegrationConfig(Config(), "dbapi", service="dbapi_service")
        # By default _dbm_propagator attribute should not be set or have a value of None.
        # DBM context propagation should be opt in.
        assert getattr(cfg, "_dbm_propagator", None) is None
        traced_cursor = TracedAsyncCursor(cursor, cfg=cfg)
        # Ensure dbm comment is not appended to sql statement
        await traced_cursor.execute("SELECT * FROM db;")
        cursor.execute.assert_called_once_with("SELECT * FROM db;")

    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="service",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    async def test_cursor_execute_with_dbm_injection(self):
        cursor = self.cursor
        cfg = IntegrationConfig(Config(), "dbapi", service="orders-db", _dbm_propagator=_DBM_Propagator(0, "query"))
        traced_cursor = TracedAsyncCursor(cursor, cfg=cfg)

        # The following operations should generate DBM comments
        await traced_cursor.execute("SELECT * FROM db;")
        await traced_cursor.executemany("SELECT * FROM db;", ())
        await traced_cursor.callproc("procedure_named_moon")

        spans = self.pop_spans()
        assert len(spans) == 3
        assert [span.resource for span in spans] == ["SELECT * FROM db;", "SELECT * FROM db;", "procedure_named_moon"]
        dbm_comment = "/*dddbs='orders-db',dde='staging',ddps='orders-app',ddpv='v7343437-d7ac743'*/ "
        cursor.execute.assert_called_once_with(dbm_comment + "SELECT * FROM db;")
        cursor.executemany.assert_called_once_with(dbm_comment + "SELECT * FROM db;", ())
        # DBM comment should not be added procedure names
        cursor.callproc.assert_called_once_with("procedure_named_moon")

    async def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.executemany.return_value = "__result__"

        traced_cursor = TracedAsyncCursor(cursor, cfg={})
        # DEV: We always pass through the result
        assert "__result__" == await traced_cursor.executemany("__query__", "arg_1", kwarg1="kwarg1")
        cursor.executemany.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    async def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = "__result__"
        traced_cursor = TracedAsyncCursor(cursor, cfg={})
        assert "__result__" == await traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        cursor.fetchone.assert_called_once_with("arg_1", kwarg1="kwarg1")

    async def test_cursor_async_connection(self):
        """Checks whether connection can execute operations with async iteration."""

        def method():
            pass

        cfg = IntegrationConfig(Config(), "db-test", service="dbapi_service")
        async with TracedAsyncCursor(self.cursor, cfg=cfg) as cursor:
            await cursor.execute("""select 'one' as x""")
            await cursor.execute("""select 'blah'""")

            async for row in cursor:
                spans = self.get_spans()
                assert len(spans) == 2
                assert spans[0].name == "postgres.query"
                assert spans[0].resource == "select ?"
                assert spans[0].service == "dbapi_service"
                assert spans[1].name == "postgres.query"
                assert spans[1].resource == "select ?"
                assert spans[1].service == "dbapi_service"

    async def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = "__result__"
        traced_cursor = TracedAsyncCursor(cursor, cfg={})
        assert "__result__" == await traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        cursor.fetchall.assert_called_once_with("arg_1", kwarg1="kwarg1")

    async def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchmany.return_value = "__result__"
        traced_cursor = TracedAsyncCursor(cursor, cfg={})
        assert "__result__" == await traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        cursor.fetchmany.assert_called_once_with("arg_1", kwarg1="kwarg1")

    async def test_correct_span_names(self):
        cursor = self.cursor
        cursor.rowcount = 0
        traced_cursor = TracedAsyncCursor(cursor, cfg={})

        await traced_cursor.execute("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query"))
        assert_is_measured(self.get_root_span())
        self.reset()

        await traced_cursor.executemany("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query"))
        assert_is_measured(self.get_root_span())
        self.reset()

        await traced_cursor.callproc("arg_1", "arg2")
        self.assert_structure(dict(name="sql.query"))
        assert_is_measured(self.get_root_span())
        self.reset()

        await traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        self.assert_has_no_spans()

        await traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        self.assert_has_no_spans()

        await traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        self.assert_has_no_spans()

    async def test_span_info(self):
        cursor = self.cursor
        cursor.rowcount = 123
        traced_cursor = TracedAsyncCursor(cursor, cfg={})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        # Only measure if the name passed matches the default name (e.g. `sql.query` and not `sql.query.fetchall`)
        assert_is_not_measured(span)
        assert span.get_tag("extra1") == "value_extra1", "Extra tags are preserved"
        assert span.name == "my_name", "Span name is respected"
        assert span.resource == "my_resource", "Resource is respected"
        assert span.span_type == "sql", "Span has the correct span type"
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"
        assert span.get_tag("component") == traced_cursor._self_config.integration_name
        assert span.get_tag("span.kind") == "client"

    async def test_cfg_service(self):
        cursor = self.cursor
        cursor.rowcount = 123
        cfg = IntegrationConfig(Config(), "db-test", service="cfg-service")
        traced_cursor = TracedAsyncCursor(cursor, cfg=cfg)

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.service == "cfg-service"

    async def test_default_service(self):
        cursor = self.cursor
        cursor.rowcount = 123
        traced_cursor = TracedAsyncCursor(cursor, cfg={})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.service == "db"

    async def test_default_service_cfg(self):
        cursor = self.cursor
        cursor.rowcount = 123
        cfg = IntegrationConfig(Config(), "db-test", _default_service="default-svc")
        traced_cursor = TracedAsyncCursor(cursor, cfg=cfg)

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.service == "default-svc"

    async def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        # Django integration used to have its own TracedAsyncCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        cfg = IntegrationConfig(Config(), "db-test")
        traced_cursor = TracedAsyncCursor(cursor, cfg=cfg)

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"


class TestFetchTracedAsyncCursor(AsyncioTestCase):
    def setUp(self):
        super(TestFetchTracedAsyncCursor, self).setUp()
        self.cursor = mock.AsyncMock()
        self.config = IntegrationConfig(Config(), "db-test", _default_service="default-svc")

    async def test_execute_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"

        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})
        assert "__result__" == await traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")
        cursor.execute.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    async def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.executemany.return_value = "__result__"

        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})
        assert "__result__" == await traced_cursor.executemany("__query__", "arg_1", kwarg1="kwarg1")
        cursor.executemany.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    async def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = "__result__"
        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})
        assert "__result__" == await traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        cursor.fetchone.assert_called_once_with("arg_1", kwarg1="kwarg1")

    async def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = "__result__"
        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})
        assert "__result__" == await traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        cursor.fetchall.assert_called_once_with("arg_1", kwarg1="kwarg1")

    async def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchmany.return_value = "__result__"
        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})
        assert "__result__" == await traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        cursor.fetchmany.assert_called_once_with("arg_1", kwarg1="kwarg1")

    async def test_correct_span_names(self):
        cursor = self.cursor
        cursor.rowcount = 0
        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})

        await traced_cursor.execute("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query"))
        self.reset()

        await traced_cursor.executemany("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query"))
        self.reset()

        await traced_cursor.callproc("arg_1", "arg2")
        self.assert_structure(dict(name="sql.query"))
        self.reset()

        await traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query.fetchone"))
        self.reset()

        await traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query.fetchmany"))
        self.reset()

        await traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        self.assert_structure(dict(name="sql.query.fetchall"))
        self.reset()

    async def test_span_info(self):
        cursor = self.cursor
        cursor.rowcount = 123
        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.get_tag("extra1") == "value_extra1", "Extra tags are preserved"
        assert span.name == "my_name", "Span name is respected"
        assert span.resource == "my_resource", "Resource is respected"
        assert span.span_type == "sql", "Span has the correct span type"
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"
        assert span.get_tag("component") == traced_cursor._self_config.integration_name
        assert span.get_tag("span.kind") == "client"

    async def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        # Django integration used to have its own FetchTracedAsyncCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"

    async def test_unknown_rowcount(self):
        class Unknown(object):
            pass

        cursor = self.cursor
        cursor.rowcount = Unknown()
        traced_cursor = FetchTracedAsyncCursor(cursor, cfg={})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span: Span = self.pop_spans()[0]
        assert span.get_metric("db.row_count") is None

    async def test_callproc_can_handle_arbitrary_args(self):
        cursor = self.cursor
        cursor.callproc.return_value = "gme --> moon"
        traced_cursor = TracedAsyncCursor(cursor, cfg={})

        await traced_cursor.callproc("proc_name", "arg_1")
        spans = self.pop_spans()
        assert len(spans) == 1
        self.reset()

        await traced_cursor.callproc("proc_name", "arg_1", "arg_2")
        spans = self.pop_spans()
        assert len(spans) == 1
        self.reset()

        await traced_cursor.callproc("proc_name", "arg_1", "arg_2", {"arg_key": "arg_value"})
        spans = self.pop_spans()
        assert len(spans) == 1
        self.reset()

    @AsyncioTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_DBM_PROPAGATION_MODE="full",
            DD_SERVICE="orders-app",
            DD_ENV="staging",
            DD_VERSION="v7343437-d7ac743",
        )
    )
    async def test_cursor_execute_fetch_with_dbm_injection(self):
        cursor = self.cursor
        dbm_propagator = _DBM_Propagator(0, "query")
        cfg = IntegrationConfig(Config(), "dbapi", service="dbapi_service", _dbm_propagator=dbm_propagator)
        traced_cursor = FetchTracedAsyncCursor(cursor, cfg=cfg)

        # The following operations should not generate DBM comments
        await traced_cursor.fetchone()
        await traced_cursor.fetchall()
        await traced_cursor.fetchmany(1)
        await traced_cursor.callproc("proc")
        cursor.fetchone.assert_called_once_with()
        cursor.fetchall.assert_called_once_with()
        cursor.fetchmany.assert_called_once_with(1)
        cursor.callproc.assert_called_once_with("proc")

        spans = self.pop_spans()
        assert len(spans) == 4
        assert all(span.get_tag("_dd.dbm_trace_injected") is None for span in spans)

        # The following operations should generate DBM comments
        await traced_cursor.execute("SELECT * FROM db;")
        await traced_cursor.executemany("SELECT * FROM db;", ())

        spans = self.pop_spans()
        assert len(spans) == 2
        assert [span.resource for span in spans] == ["SELECT * FROM db;", "SELECT * FROM db;"]
        assert all(span.get_tag("_dd.dbm_trace_injected") == "true" for span in spans)
        dbm_comment_exc = dbm_propagator._get_dbm_comment(spans[0])
        cursor.execute.assert_called_once_with(dbm_comment_exc + "SELECT * FROM db;")
        dbm_comment_excmany = dbm_propagator._get_dbm_comment(spans[1])
        cursor.executemany.assert_called_once_with(dbm_comment_excmany + "SELECT * FROM db;", ())


class TestTracedAsyncConnection(AsyncioTestCase):
    def setUp(self):
        super(TestTracedAsyncConnection, self).setUp()
        self.connection = mock.AsyncMock()

    async def test_cursor_class(self):
        # Default
        traced_connection = TracedAsyncConnection(self.connection)
        self.assertTrue(traced_connection._self_cursor_cls is TracedAsyncCursor)

        # Trace fetched methods
        with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
            traced_connection = TracedAsyncConnection(self.connection)
            self.assertTrue(traced_connection._self_cursor_cls is FetchTracedAsyncCursor)

        # Manually provided cursor class
        with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
            traced_connection = TracedAsyncConnection(self.connection, cursor_cls=TracedAsyncCursor)
            self.assertTrue(traced_connection._self_cursor_cls is TracedAsyncCursor)

    async def test_commit_is_traced(self):
        connection = self.connection
        connection.commit.return_value = None
        traced_connection = TracedAsyncConnection(connection)
        await traced_connection.commit()
        assert self.pop_spans()[0].name == "mock.connection.commit"
        connection.commit.assert_called_with()

    async def test_rollback_is_traced(self):
        connection = self.connection
        connection.rollback.return_value = None
        traced_connection = TracedAsyncConnection(connection)
        await traced_connection.rollback()
        assert self.pop_spans()[0].name == "mock.connection.rollback"
        connection.rollback.assert_called_with()

    async def test_connection_context_manager(self):
        class Cursor(object):
            rowcount = 0

            async def execute(self, *args, **kwargs):
                pass

            async def fetchall(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def commit(self, *args, **kwargs):
                pass

        # When a connection is returned from a context manager the object proxy
        # should be returned so that tracing works.

        class ConnectionConnection(object):
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

            async def commit(self):
                pass

        conn = TracedAsyncConnection(ConnectionConnection())
        async with conn as conn2:
            await conn2.commit()
        spans = self.pop_spans()
        assert len(spans) == 1

        async with conn as conn2:
            async with conn2.cursor() as cursor:
                await cursor.execute("query")
                await cursor.fetchall()

        spans = self.pop_spans()
        assert len(spans) == 1

        # If a cursor is returned from the context manager
        # then it should be instrumented.

        class ConnectionCursor(object):
            async def __aenter__(self):
                return Cursor()

            async def __aexit__(self, *exc):
                return False

            async def commit(self):
                pass

        async with TracedAsyncConnection(ConnectionCursor()) as cursor:
            await cursor.execute("query")
            await cursor.fetchall()
        spans = self.pop_spans()
        assert len(spans) == 1

        # If a traced cursor is returned then it should not
        # be double instrumented.

        class ConnectionTracedAsyncCursor(object):
            async def __aenter__(self):
                return self.cursor()

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return TracedAsyncCursor(Cursor(), {})

            async def commit(self):
                pass

        async with TracedAsyncConnection(ConnectionTracedAsyncCursor()) as cursor:
            await cursor.execute("query")
            await cursor.fetchall()
        spans = self.pop_spans()
        assert len(spans) == 1

        # Check when a different connection object is returned
        # from a connection context manager.
        # No traces should be produced.

        other_conn = ConnectionConnection()

        class ConnectionDifferentConnection(object):
            async def __aenter__(self):
                return other_conn

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

            async def commit(self):
                pass

        conn = TracedAsyncConnection(ConnectionDifferentConnection())
        async with conn as conn2:
            await conn2.commit()
        spans = self.pop_spans()
        assert len(spans) == 0

        async with conn as conn2:
            async with conn2.cursor() as cursor:
                await cursor.execute("query")
                await cursor.fetchall()

        spans = self.pop_spans()
        assert len(spans) == 0

        # When some unexpected value is returned from the context manager
        # it should be handled gracefully.

        class ConnectionUnknown(object):
            async def __aenter__(self):
                return 123456

            async def __aexit__(self, *exc):
                return False

            def cursor(self):
                return Cursor()

            async def commit(self):
                pass

        conn = TracedAsyncConnection(ConnectionDifferentConnection())
        async with conn as conn2:
            await conn2.commit()
        spans = self.pop_spans()
        assert len(spans) == 0

        async with conn as conn2:
            async with conn2.cursor() as cursor:
                await cursor.execute("query")
                await cursor.fetchall()

        spans = self.pop_spans()
        assert len(spans) == 0

        # Errors should be the same when no context management is defined.

        class ConnectionNoCtx(object):
            async def cursor(self):
                return Cursor()

            async def commit(self):
                pass

        conn = TracedAsyncConnection(ConnectionNoCtx())
        with pytest.raises(AttributeError):
            with conn:
                pass

        with pytest.raises(AttributeError):
            with conn as conn2:
                pass

        spans = self.pop_spans()
        assert len(spans) == 0

    async def test_cursor_wraps_awaitable_cursor(self):
        class Cursor(object):
            rowcount = 0

            async def execute(self, *args, **kwargs):
                pass

        class ConnectionAwaitableCursor(object):
            async def cursor(self):
                return Cursor()

        conn = TracedAsyncConnection(ConnectionAwaitableCursor())
        cursor = await conn.cursor()
        assert isinstance(cursor, TracedAsyncCursor)

        await cursor.execute("query")
        spans = self.pop_spans()
        assert len(spans) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["execute", "executemany", "callproc", "fetchone"])
@pytest.mark.parametrize("failure_type", [None, ValueError, asyncio.CancelledError])
async def test_post_execute_hook_receives_active_span(tracer, test_spans, method_name, failure_type):
    hook_spans = []

    class ExtendedCursor(FetchTracedAsyncCursor):
        def _set_post_execute_tags(self, span: Span) -> None:
            super()._set_post_execute_tags(span)
            assert span is tracer.current_span()
            assert not span.finished
            span.set_tag("driver.query_id", self.__wrapped__.query_id)
            hook_spans.append(span)

    result = object()
    failure = failure_type("driver execution failed") if failure_type else None
    cursor = mock.AsyncMock(rowcount=7, query_id="query-123")
    method = getattr(cursor, method_name)
    method.return_value = result
    method.side_effect = failure
    traced = ExtendedCursor(cursor)
    query = "SELECT 1"
    traced._self_last_execute_operation = query
    args = () if method_name == "fetchone" else (query,)
    execution_context = core.current

    with tracer.trace("parent") as parent:
        if failure_type:
            with pytest.raises(failure_type) as caught:
                await getattr(traced, method_name)(*args)
            assert caught.value is failure
        else:
            assert await getattr(traced, method_name)(*args) is result
        assert tracer.current_span() is parent
        assert core.current is execution_context
        assert len(hook_spans) == 1
        span = hook_spans[0]
        assert span.finished
        assert span.parent_id == parent.span_id
        assert span.resource == query
        assert span.error == int(failure_type is not None)
        assert span.get_metric("db.row_count") == 7
        assert span.get_tag("driver.query_id") == "query-123"
        if failure_type:
            assert span.get_tag("error.message") == str(failure)

    assert test_spans.spans == [parent, span]


@pytest.mark.asyncio
@pytest.mark.parametrize("method_name", ["commit", "rollback"])
@pytest.mark.parametrize("failure_type", [ValueError, asyncio.CancelledError])
async def test_transaction_failure_restores_parent(tracer, test_spans, method_name, failure_type):
    failure = failure_type("transaction failed")
    connection = mock.AsyncMock()
    getattr(connection, method_name).side_effect = failure
    traced = TracedAsyncConnection(connection)
    execution_context = core.current

    with tracer.trace("parent") as parent:
        with pytest.raises(failure_type) as caught:
            await getattr(traced, method_name)()
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


@pytest.mark.asyncio
async def test_disabled_tracing_calls_driver_without_database_context(tracer, test_spans):
    result = object()
    cursor = mock.AsyncMock(rowcount=1)
    cursor.execute.return_value = result
    with mock.patch.object(tracer, "enabled", False):
        with mock.patch.object(core, "context_with_event", wraps=core.context_with_event) as context:
            assert await TracedAsyncCursor(cursor).execute("SELECT 1") is result
    cursor.execute.assert_awaited_once_with("SELECT 1")
    context.assert_not_called()
    assert test_spans.spans == []


@pytest.mark.asyncio
async def test_result_contexts_are_isolated_between_tasks(tracer, test_spans):
    ready = asyncio.Event()
    entered = []

    async def run_query(query, row_count):
        cursor = mock.AsyncMock(rowcount=row_count)

        async def execute(statement):
            entered.append(statement)
            if len(entered) == 2:
                ready.set()
            await ready.wait()
            return statement

        cursor.execute.side_effect = execute
        return await TracedAsyncCursor(cursor).execute(query)

    with tracer.trace("parent") as parent:
        assert await asyncio.gather(run_query("SELECT 1", 1), run_query("SELECT 2", 2)) == ["SELECT 1", "SELECT 2"]
        assert tracer.current_span() is parent

    spans = test_spans.spans
    assert len(spans) == 3
    results = {span.resource: span for span in spans if span.name == "sql.query"}
    assert set(results) == {"SELECT 1", "SELECT 2"}
    for query, row_count in (("SELECT 1", 1), ("SELECT 2", 2)):
        assert results[query].parent_id == parent.span_id
        assert results[query].get_metric("db.row_count") == row_count
        assert results[query].finished
