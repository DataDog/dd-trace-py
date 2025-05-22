import mock
import pytest

from ddtrace.contrib.dbapi_async import FetchTracedAsyncCursor
from ddtrace.contrib.dbapi_async import TracedAsyncConnection
from ddtrace.contrib.dbapi_async import TracedAsyncCursor
from ddtrace.propagation._database_monitoring import _DBM_Propagator
from ddtrace.settings._config import Config
from ddtrace.settings.integration import IntegrationConfig
from ddtrace.trace import Pin
from ddtrace.trace import Span  # noqa:F401
from tests.contrib.asyncio.utils import AsyncioTestCase
from tests.contrib.asyncio.utils import mark_asyncio
from tests.utils import assert_is_measured
from tests.utils import assert_is_not_measured


class TestTracedAsyncCursor(AsyncioTestCase):
    def setUp(self):
        super(TestTracedAsyncCursor, self).setUp()
        self.cursor = mock.AsyncMock()

    @mark_asyncio
    async def test_execute_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"

        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, {})
        # DEV: We always pass through the result
        assert "__result__" == await traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")
        cursor.execute.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    @AsyncioTestCase.run_in_subprocess(env_overrides=dict(DD_DBM_PROPAGATION_MODE="full"))
    @mark_asyncio
    async def test_dbm_propagation_not_supported(self):
        cursor = self.cursor
        cfg = IntegrationConfig(Config(), "dbapi", service="dbapi_service")
        # By default _dbm_propagator attribute should not be set or have a value of None.
        # DBM context propagation should be opt in.
        assert getattr(cfg, "_dbm_propagator", None) is None
        pin = Pin(service="dbapi_service")
        pin._tracer = self.tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, cfg)
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
    @mark_asyncio
    async def test_cursor_execute_with_dbm_injection(self):
        cursor = self.cursor
        cfg = IntegrationConfig(Config(), "dbapi", service="orders-db", _dbm_propagator=_DBM_Propagator(0, "query"))
        pin = Pin(service="orders-db")
        pin._tracer = self.tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, cfg)

        # The following operations should generate DBM comments
        await traced_cursor.execute("SELECT * FROM db;")
        await traced_cursor.executemany("SELECT * FROM db;", ())
        await traced_cursor.callproc("procedure_named_moon")

        spans = self.tracer.pop()
        assert len(spans) == 3
        dbm_comment = "/*dddbs='orders-db',dde='staging',ddps='orders-app',ddpv='v7343437-d7ac743'*/ "
        cursor.execute.assert_called_once_with(dbm_comment + "SELECT * FROM db;")
        cursor.executemany.assert_called_once_with(dbm_comment + "SELECT * FROM db;", ())
        # DBM comment should not be added procedure names
        cursor.callproc.assert_called_once_with("procedure_named_moon")

    @mark_asyncio
    async def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.executemany.return_value = "__result__"

        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, {})
        # DEV: We always pass through the result
        assert "__result__" == await traced_cursor.executemany("__query__", "arg_1", kwarg1="kwarg1")
        cursor.executemany.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = "__result__"
        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, {})
        assert "__result__" == await traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        cursor.fetchone.assert_called_once_with("arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_cursor_async_connection(self):
        """Checks whether connection can execute operations with async iteration."""

        def method():
            pass

        pin = Pin("dbapi_service")
        pin._tracer = self.tracer
        async with TracedAsyncCursor(self.cursor, pin, {}) as cursor:
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

    @mark_asyncio
    async def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = "__result__"
        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, {})
        assert "__result__" == await traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        cursor.fetchall.assert_called_once_with("arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchmany.return_value = "__result__"
        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, {})
        assert "__result__" == await traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        cursor.fetchmany.assert_called_once_with("arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_correct_span_names(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        pin = Pin("pin_name")
        pin._tracer = tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, {})

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

    @mark_asyncio
    async def test_when_pin_disabled_then_no_tracing(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"
        cursor.executemany.return_value = "__result__"

        tracer.enabled = False
        pin = Pin("pin_name")
        pin._tracer = tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, {})

        assert "__result__" == await traced_cursor.execute("arg_1", kwarg1="kwarg1")
        assert len(tracer.pop()) == 0

        assert "__result__" == await traced_cursor.executemany("arg_1", kwarg1="kwarg1")
        assert len(tracer.pop()) == 0

        cursor.callproc.return_value = "callproc"
        assert "callproc" == await traced_cursor.callproc("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchone.return_value = "fetchone"
        assert "fetchone" == await traced_cursor.fetchone("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchmany.return_value = "fetchmany"
        assert "fetchmany" == await traced_cursor.fetchmany("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchall.return_value = "fetchall"
        assert "fetchall" == await traced_cursor.fetchall("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

    @mark_asyncio
    async def test_span_info(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin("my_service", tags={"pin1": "value_pin1"})
        pin._tracer = tracer
        traced_cursor = TracedAsyncCursor(cursor, pin, {})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
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
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"
        assert span.get_tag("component") == traced_cursor._self_config.integration_name
        assert span.get_tag("span.kind") == "client"

    @mark_asyncio
    async def test_cfg_service(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin(None, tags={"pin1": "value_pin1"})
        pin._tracer = tracer
        cfg = IntegrationConfig(Config(), "db-test", service="cfg-service")
        traced_cursor = TracedAsyncCursor(cursor, pin, cfg)

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span = tracer.pop()[0]  # type: Span
        assert span.service == "cfg-service"

    @mark_asyncio
    async def test_default_service(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin(None, tags={"pin1": "value_pin1"})
        pin._tracer = tracer

        traced_cursor = TracedAsyncCursor(cursor, pin, {})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span = tracer.pop()[0]  # type: Span
        assert span.service == "db"

    @mark_asyncio
    async def test_default_service_cfg(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin(None, tags={"pin1": "value_pin1"})
        pin._tracer = tracer
        cfg = IntegrationConfig(Config(), "db-test", _default_service="default-svc")
        traced_cursor = TracedAsyncCursor(cursor, pin, cfg)

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span = tracer.pop()[0]  # type: Span
        assert span.service == "default-svc"

    @mark_asyncio
    async def test_service_cfg_and_pin(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin("pin-svc", tags={"pin1": "value_pin1"})
        pin._tracer = tracer
        cfg = IntegrationConfig(Config(), "db-test", _default_service="default-svc")
        traced_cursor = TracedAsyncCursor(cursor, pin, cfg)

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span = tracer.pop()[0]  # type: Span
        assert span.service == "pin-svc"

    @mark_asyncio
    async def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        tracer = self.tracer
        # Django integration used to have its own TracedAsyncCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        pin = Pin("my_service", tags={"pin1": "value_pin1"})
        pin._tracer = tracer
        cfg = IntegrationConfig(Config(), "db-test")
        traced_cursor = TracedAsyncCursor(cursor, pin, cfg)

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span = tracer.pop()[0]  # type: Span
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"


class TestFetchTracedAsyncCursor(AsyncioTestCase):
    def setUp(self):
        super(TestFetchTracedAsyncCursor, self).setUp()
        self.cursor = mock.AsyncMock()
        self.config = IntegrationConfig(Config(), "db-test", _default_service="default-svc")

    @mark_asyncio
    async def test_execute_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"

        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})
        assert "__result__" == await traced_cursor.execute("__query__", "arg_1", kwarg1="kwarg1")
        cursor.execute.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_executemany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.executemany.return_value = "__result__"

        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})
        assert "__result__" == await traced_cursor.executemany("__query__", "arg_1", kwarg1="kwarg1")
        cursor.executemany.assert_called_once_with("__query__", "arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_fetchone_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchone.return_value = "__result__"
        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})
        assert "__result__" == await traced_cursor.fetchone("arg_1", kwarg1="kwarg1")
        cursor.fetchone.assert_called_once_with("arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_fetchall_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchall.return_value = "__result__"
        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})
        assert "__result__" == await traced_cursor.fetchall("arg_1", kwarg1="kwarg1")
        cursor.fetchall.assert_called_once_with("arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_fetchmany_wrapped_is_called_and_returned(self):
        cursor = self.cursor
        cursor.rowcount = 0
        cursor.fetchmany.return_value = "__result__"
        pin = Pin("pin_name")
        pin._tracer = self.tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})
        assert "__result__" == await traced_cursor.fetchmany("arg_1", kwarg1="kwarg1")
        cursor.fetchmany.assert_called_once_with("arg_1", kwarg1="kwarg1")

    @mark_asyncio
    async def test_correct_span_names(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        pin = Pin("pin_name")
        pin._tracer = tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})

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

    @mark_asyncio
    async def test_when_pin_disabled_then_no_tracing(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 0
        cursor.execute.return_value = "__result__"
        cursor.executemany.return_value = "__result__"

        tracer.enabled = False
        pin = Pin("pin_name")
        pin._tracer = tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})

        assert "__result__" == await traced_cursor.execute("arg_1", kwarg1="kwarg1")
        assert len(tracer.pop()) == 0

        assert "__result__" == await traced_cursor.executemany("arg_1", kwarg1="kwarg1")
        assert len(tracer.pop()) == 0

        cursor.callproc.return_value = "callproc"
        assert "callproc" == await traced_cursor.callproc("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchone.return_value = "fetchone"
        assert "fetchone" == await traced_cursor.fetchone("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchmany.return_value = "fetchmany"
        assert "fetchmany" == await traced_cursor.fetchmany("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

        cursor.fetchall.return_value = "fetchall"
        assert "fetchall" == await traced_cursor.fetchall("arg_1", "arg_2")
        assert len(tracer.pop()) == 0

    @mark_asyncio
    async def test_span_info(self):
        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = 123
        pin = Pin("my_service", tags={"pin1": "value_pin1"})
        pin._tracer = tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span = tracer.pop()[0]  # type: Span
        assert span.get_tag("pin1") == "value_pin1", "Pin tags are preserved"
        assert span.get_tag("extra1") == "value_extra1", "Extra tags are merged into pin tags"
        assert span.name == "my_name", "Span name is respected"
        assert span.service == "my_service", "Service from pin"
        assert span.resource == "my_resource", "Resource is respected"
        assert span.span_type == "sql", "Span has the correct span type"
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"
        assert span.get_tag("component") == traced_cursor._self_config.integration_name
        assert span.get_tag("span.kind") == "client"

    @mark_asyncio
    async def test_django_traced_cursor_backward_compatibility(self):
        cursor = self.cursor
        tracer = self.tracer
        # Django integration used to have its own FetchTracedAsyncCursor implementation. When we replaced such custom
        # implementation with the generic dbapi traced cursor, we had to make sure to add the tag 'sql.rows' that was
        # set by the legacy replaced implementation.
        cursor.rowcount = 123
        pin = Pin("my_service", tags={"pin1": "value_pin1"})
        pin._tracer = tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span = tracer.pop()[0]  # type: Span
        # Row count
        assert span.get_metric("db.row_count") == 123, "Row count is set as a metric"

    @mark_asyncio
    async def test_unknown_rowcount(self):
        class Unknown(object):
            pass

        cursor = self.cursor
        tracer = self.tracer
        cursor.rowcount = Unknown()
        pin = Pin("my_service", tags={"pin1": "value_pin1"})
        pin._tracer = tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, {})

        async def method():
            pass

        await traced_cursor._trace_method(method, "my_name", "my_resource", {"extra1": "value_extra1"}, False)
        span = tracer.pop()[0]  # type: Span
        assert span.get_metric("db.row_count") is None

    @mark_asyncio
    async def test_callproc_can_handle_arbitrary_args(self):
        cursor = self.cursor
        tracer = self.tracer
        pin = Pin("pin_name")
        pin._tracer = tracer
        cursor.callproc.return_value = "gme --> moon"
        traced_cursor = TracedAsyncCursor(cursor, pin, {})

        await traced_cursor.callproc("proc_name", "arg_1")
        spans = self.tracer.pop()
        assert len(spans) == 1
        self.reset()

        await traced_cursor.callproc("proc_name", "arg_1", "arg_2")
        spans = self.tracer.pop()
        assert len(spans) == 1
        self.reset()

        await traced_cursor.callproc("proc_name", "arg_1", "arg_2", {"arg_key": "arg_value"})
        spans = self.tracer.pop()
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
    @mark_asyncio
    async def test_cursor_execute_fetch_with_dbm_injection(self):
        cursor = self.cursor
        dbm_propagator = _DBM_Propagator(0, "query")
        cfg = IntegrationConfig(Config(), "dbapi", service="dbapi_service", _dbm_propagator=dbm_propagator)
        pin = Pin("dbapi_service")
        pin._tracer = self.tracer
        traced_cursor = FetchTracedAsyncCursor(cursor, pin, cfg)

        # The following operations should not generate DBM comments
        await traced_cursor.fetchone()
        await traced_cursor.fetchall()
        await traced_cursor.fetchmany(1)
        await traced_cursor.callproc("proc")
        cursor.fetchone.assert_called_once_with()
        cursor.fetchall.assert_called_once_with()
        cursor.fetchmany.assert_called_once_with(1)
        cursor.callproc.assert_called_once_with("proc")

        spans = self.tracer.pop()
        assert len(spans) == 4

        # The following operations should generate DBM comments
        await traced_cursor.execute("SELECT * FROM db;")
        await traced_cursor.executemany("SELECT * FROM db;", ())

        spans = self.tracer.pop()
        assert len(spans) == 2
        dbm_comment_exc = dbm_propagator._get_dbm_comment(spans[0])
        cursor.execute.assert_called_once_with(dbm_comment_exc + "SELECT * FROM db;")
        dbm_comment_excmany = dbm_propagator._get_dbm_comment(spans[1])
        cursor.executemany.assert_called_once_with(dbm_comment_excmany + "SELECT * FROM db;", ())


class TestTracedAsyncConnection(AsyncioTestCase):
    def setUp(self):
        super(TestTracedAsyncConnection, self).setUp()
        self.connection = mock.AsyncMock()

    @mark_asyncio
    async def test_cursor_class(self):
        pin = Pin("pin_name")
        pin._tracer = self.tracer

        # Default
        traced_connection = TracedAsyncConnection(self.connection, pin=pin)
        self.assertTrue(traced_connection._self_cursor_cls is TracedAsyncCursor)

        # Trace fetched methods
        with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
            traced_connection = TracedAsyncConnection(self.connection, pin=pin)
            self.assertTrue(traced_connection._self_cursor_cls is FetchTracedAsyncCursor)

        # Manually provided cursor class
        with self.override_config("dbapi2", dict(trace_fetch_methods=True)):
            traced_connection = TracedAsyncConnection(self.connection, pin=pin, cursor_cls=TracedAsyncCursor)
            self.assertTrue(traced_connection._self_cursor_cls is TracedAsyncCursor)

    @mark_asyncio
    async def test_commit_is_traced(self):
        connection = self.connection
        tracer = self.tracer
        connection.commit.return_value = None
        pin = Pin("pin_name")
        pin._tracer = tracer
        traced_connection = TracedAsyncConnection(connection, pin)
        await traced_connection.commit()
        assert tracer.pop()[0].name == "mock.connection.commit"
        connection.commit.assert_called_with()

    @mark_asyncio
    async def test_rollback_is_traced(self):
        connection = self.connection
        tracer = self.tracer
        connection.rollback.return_value = None
        pin = Pin("pin_name")
        pin._tracer = tracer
        traced_connection = TracedAsyncConnection(connection, pin)
        await traced_connection.rollback()
        assert tracer.pop()[0].name == "mock.connection.rollback"
        connection.rollback.assert_called_with()

    @mark_asyncio
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

        pin = Pin("pin")
        pin._tracer = self.tracer
        conn = TracedAsyncConnection(ConnectionConnection(), pin)
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

        async with TracedAsyncConnection(ConnectionCursor(), pin) as cursor:
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
                return TracedAsyncCursor(Cursor(), pin, {})

            async def commit(self):
                pass

        async with TracedAsyncConnection(ConnectionTracedAsyncCursor(), pin) as cursor:
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

        conn = TracedAsyncConnection(ConnectionDifferentConnection(), pin)
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

        conn = TracedAsyncConnection(ConnectionDifferentConnection(), pin)
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

        conn = TracedAsyncConnection(ConnectionNoCtx(), pin)
        with pytest.raises(AttributeError):
            with conn:
                pass

        with pytest.raises(AttributeError):
            with conn as conn2:
                pass

        spans = self.pop_spans()
        assert len(spans) == 0
