"""
Generic dbapi tracing code.
"""

from collections.abc import Iterator
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any
from typing import Callable
from typing import Optional
from typing import cast

import wrapt

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib._events.dbapi import DatabaseOperationEvent
from ddtrace.contrib._events.dbapi import DatabaseQueryPrepareEvent
from ddtrace.contrib._events.dbapi import DatabaseResultEvent
from ddtrace.contrib._events.dbapi import DbQueryEvent
from ddtrace.internal import core
from ddtrace.internal.database import configure_database
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils import ArgumentError
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.deprecations import deprecate
from ddtrace.trace import Span

from ..ext import db
from ..ext import sql
from .internal.trace_utils import ext_service
from .internal.trace_utils import is_tracing_enabled
from .internal.trace_utils import iswrapped


log = get_logger(__name__)
_UNSET = object()


config._add(
    "dbapi2",
    dict(
        _default_service="db",
        _dbapi_span_name_prefix="sql",
        trace_fetch_methods=None,  # Part of the API. Should be implemented at the integration level.
    ),
)


def get_version() -> str:
    return ""


class TracedCursor(wrapt.ObjectProxy):
    """TracedCursor wraps a psql cursor and traces its queries."""

    def __init__(
        self,
        cursor,
        pin: Optional[Pin] = None,
        cfg: Optional[Mapping[str, str]] = None,
        db_tags: Optional[Mapping[str, str]] = None,
    ):
        if pin is not None:
            deprecate(
                "The pin parameter is deprecated",
                message="This parameter has no effect.",
                category=DDTraceDeprecationWarning,
                removal_version="5.0.0",
            )

        super().__init__(cursor)

        # Allow dbapi-based integrations to override default span name prefix
        span_name_prefix = (
            cfg["_dbapi_span_name_prefix"]
            if cfg and "_dbapi_span_name_prefix" in cfg
            else config.dbapi2["_dbapi_span_name_prefix"]
        )
        span_name = (
            cfg["_dbapi_span_operation_name"]
            if cfg and "_dbapi_span_operation_name" in cfg
            else f"{span_name_prefix}.query"
        )
        self._self_datadog_name = span_name
        self._self_dbapi_span_name_prefix = span_name_prefix
        self._self_last_execute_operation = None
        self._self_config = cast(IntegrationConfig, cfg or config.dbapi2)
        # Retain the legacy cursor extension interface; propagation remains listener-owned.
        self._self_dbm_propagator_override = _UNSET
        self._self_db_tags = dict(db_tags) if db_tags else {}

    @property
    def _self_dbm_propagator(self) -> Any:
        if self._self_dbm_propagator_override is not _UNSET:
            return self._self_dbm_propagator_override
        if "_dbm_propagator" not in self._self_config:
            configure_database(self._self_config)
        return getattr(self._self_config, "_dbm_propagator", None)

    @_self_dbm_propagator.setter
    def _self_dbm_propagator(self, value: Any) -> None:
        self._self_dbm_propagator_override = value

    def __iter__(self):
        return self.__wrapped__.__iter__()

    def __next__(self):
        return self.__wrapped__.__next__()

    def _trace_method(
        self,
        method: Callable[..., Any],
        name: str,
        resource: Any,
        extra_tags: dict[str, Any],
        dbm_propagator: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        if not is_tracing_enabled():
            return method(*args, **kwargs)
        with self._trace_method_context(method, name, resource, extra_tags, dbm_propagator, args, kwargs) as (
            args,
            kwargs,
        ):
            return method(*args, **kwargs)

    @contextmanager
    def _trace_method_context(
        self,
        method: Callable[..., Any],
        name: str,
        resource: Any,
        extra_tags: dict[str, Any],
        dbm_propagator: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Iterator[tuple[tuple[Any, ...], dict[str, Any]]]:
        """Share query preparation and result publication around sync and async driver calls."""
        with core.context_with_event(
            DatabaseOperationEvent(
                component=self._self_config.integration_name,
                integration_config=self._self_config,
                operation_name=name,
                service=ext_service(None, self._self_config),
                resource=resource,
                measured=name == self._self_datadog_name,
                connection_tags=self._self_db_tags,
                tags=extra_tags,
            )
        ) as ctx:
            # No reason to tag the query since it is set as the resource by the agent. See:
            # https://github.com/DataDog/datadog-trace-agent/blob/bda1ebbf170dd8c5879be993bdd4dbae70d10fda/obfuscate/sql.go#L232
            # Security and IAST validations
            core.dispatch("db_query_check", (args, kwargs, self._self_config.integration_name, method))

            if core.has_listeners(DatabaseQueryPrepareEvent.event_name):
                if "_dbm_propagator" not in self._self_config:
                    configure_database(self._self_config)
                supports_query_rewriting = bool(dbm_propagator)
                if dbm_propagator is None and getattr(self._self_config, "_dbm_propagator", None) is None:
                    supports_query_rewriting = method in (
                        getattr(self.__wrapped__, "execute", None),
                        getattr(self.__wrapped__, "executemany", None),
                    )
                if supports_query_rewriting:
                    prepare = DatabaseQueryPrepareEvent(context=ctx, args=args, kwargs=kwargs)
                    core.dispatch_event(prepare)
                    args, kwargs = prepare.args, prepare.kwargs

            driver_failed = True
            try:
                yield args, kwargs
                driver_failed = False
            finally:
                try:
                    legacy_hook = type(self)._set_post_execute_tags is not TracedCursor._default_post_execute_tags
                    if legacy_hook and type(self)._get_post_execute_result is TracedCursor._default_post_execute_result:
                        # Legacy-only overrides control whether cursor metadata can be read safely.
                        result = DatabaseResultEvent(context=ctx)
                    else:
                        result = self._get_post_execute_result(ctx)
                    if legacy_hook:
                        result.legacy_post_execute = self._set_post_execute_tags
                    core.dispatch_event(result, allow_raise=True)
                except Exception:
                    if not driver_failed:
                        raise
                    log.debug("Error publishing database result metadata", exc_info=True)

    def _dbapi_query_encoding(self) -> Optional[str]:
        """Return the connection's Python codec, or None when it is unknown."""
        return None

    def executemany(self, query, *args, **kwargs):
        """Wraps the cursor.executemany method"""
        self._self_last_execute_operation = query
        if isinstance(query, (str, bytes, bytearray, memoryview)):
            core.dispatch_event(
                DbQueryEvent(
                    query=query,
                    span_name_prefix=self._self_dbapi_span_name_prefix,
                    get_encoding=self._dbapi_query_encoding,
                )
            )
        # Always return the result as-is
        # DEV: Some libraries return `None`, others `int`, and others the cursor objects
        #      These differences should be overridden at the integration specific layer (e.g. in `sqlite3/patch.py`)
        # FIXME[matt] properly handle kwargs here. arg names can be different
        # with different libs.
        return self._trace_method(
            self.__wrapped__.executemany,
            self._self_datadog_name,
            query,
            {"sql.executemany": "true"},
            self._self_dbm_propagator,
            query,
            *args,
            **kwargs,
        )

    def execute(self, query, *args, **kwargs):
        """Wraps the cursor.execute method"""
        self._self_last_execute_operation = query
        if isinstance(query, (str, bytes, bytearray, memoryview)):
            core.dispatch_event(
                DbQueryEvent(
                    query=query,
                    span_name_prefix=self._self_dbapi_span_name_prefix,
                    get_encoding=self._dbapi_query_encoding,
                )
            )

        # Always return the result as-is
        # DEV: Some libraries return `None`, others `int`, and others the cursor objects
        #      These differences should be overridden at the integration specific layer (e.g. in `sqlite3/patch.py`)
        return self._trace_method(
            self.__wrapped__.execute,
            self._self_datadog_name,
            query,
            {},
            self._self_dbm_propagator,
            query,
            *args,
            **kwargs,
        )

    def callproc(self, proc, *args):
        """Wraps the cursor.callproc method"""
        self._self_last_execute_operation = proc
        return self._trace_method(self.__wrapped__.callproc, self._self_datadog_name, proc, {}, None, proc, *args)

    def _get_post_execute_result(self, context: core.ExecutionContext[DatabaseOperationEvent]) -> DatabaseResultEvent:
        return DatabaseResultEvent(
            context=context, row_count=getattr(self.__wrapped__, "rowcount", None), row_count_as_tag=True
        )

    _default_post_execute_result = _get_post_execute_result

    def _set_post_execute_tags(self, span: Span) -> None:
        """Compatibility hook for existing subclasses; new subclasses should publish result metadata."""
        # rowcount is in the dbapi specification (https://peps.python.org/pep-0249/#rowcount)
        # but some database drivers (cassandra-driver specifically) don't implement it.
        row_count = getattr(self.__wrapped__, "rowcount", None)
        if row_count is None:
            return
        # Necessary for django integration backward compatibility. Django integration used to provide its own
        # implementation of the TracedCursor, which used to store the row count into a tag instead of
        # as a metric. Such custom implementation has been replaced by this generic dbapi implementation and
        # this tag has been added since.
        # Check row count is an integer type to avoid comparison type error
        span._set_attribute(db.ROWCOUNT, row_count)
        if isinstance(row_count, int) and row_count >= 0:
            span.set_tag(db.ROWCOUNT, cast(Any, row_count))

    # Preserve the original identity so monkeypatched legacy hooks still run.
    _default_post_execute_tags = _set_post_execute_tags

    def __enter__(self):
        # previous versions of the dbapi didn't support context managers. let's
        # reference the func that would be called to ensure that errors
        # messages will be the same.
        self.__wrapped__.__enter__

        # and finally, yield the traced cursor.
        return self


class FetchTracedCursor(TracedCursor):
    """
    Sub-class of :class:`TracedCursor` that also instruments `fetchone`, `fetchall`, and `fetchmany` methods.

    We do not trace these functions by default since they can get very noisy (e.g. `fetchone` with 100k rows).
    """

    def fetchone(self, *args, **kwargs):
        """Wraps the cursor.fetchone method"""
        span_name = "{}.{}".format(self._self_datadog_name, "fetchone")
        return self._trace_method(
            self.__wrapped__.fetchone, span_name, self._self_last_execute_operation, {}, None, *args, **kwargs
        )

    def fetchall(self, *args, **kwargs):
        """Wraps the cursor.fetchall method"""
        span_name = "{}.{}".format(self._self_datadog_name, "fetchall")
        return self._trace_method(
            self.__wrapped__.fetchall, span_name, self._self_last_execute_operation, {}, None, *args, **kwargs
        )

    def fetchmany(self, *args, **kwargs):
        """Wraps the cursor.fetchmany method"""
        span_name = "{}.{}".format(self._self_datadog_name, "fetchmany")
        # We want to trace the information about how many rows were requested. Note that this number may be larger
        # the number of rows actually returned if less then requested are available from the query.
        size_tag_key = "db.fetch.size"

        try:
            extra_tags = {size_tag_key: get_argument_value(args, kwargs, 0, "size")}
        except ArgumentError:
            default_array_size = getattr(self.__wrapped__, "arraysize", None)
            extra_tags = {size_tag_key: default_array_size} if default_array_size else {}

        return self._trace_method(
            self.__wrapped__.fetchmany, span_name, self._self_last_execute_operation, extra_tags, None, *args, **kwargs
        )


class TracedConnection(wrapt.ObjectProxy):
    """TracedConnection wraps a Connection with tracing code."""

    def __init__(self, conn, pin=None, cfg=None, cursor_cls=None, db_tags: Optional[Mapping[str, str]] = None):
        if pin is not None:
            deprecate(
                "The pin parameter is deprecated",
                message="This parameter has no effect.",
                category=DDTraceDeprecationWarning,
                removal_version="5.0.0",
            )
        if not cfg:
            cfg = config.dbapi2
        # Set default cursor class if one was not provided
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = FetchTracedCursor if cfg.trace_fetch_methods else TracedCursor

        super().__init__(conn)
        name = _get_vendor(conn)
        self._self_datadog_name = f"{name}.connection"
        # wrapt requires prefix of `_self` for attributes that are only in the
        # proxy (since some of our source objects will use `__slots__`)
        self._self_cursor_cls = cursor_cls
        self._self_config = cfg
        self._self_db_tags = dict(db_tags) if db_tags else {}

    def __enter__(self):
        """Context management is not defined by the dbapi spec.

        This means unfortunately that the database clients each define their own
        implementations.

        The ones we know about are:

        - mysqlclient<2.0 which returns a cursor instance. >=2.0 returns a
          connection instance.
        - psycopg returns a connection.
        - pyodbc returns a connection.
        - pymysql doesn't implement it.
        - sqlite3 returns the connection.
        """
        r = self.__wrapped__.__enter__()

        if hasattr(r, "cursor"):
            # r is Connection-like.
            if r is self.__wrapped__:
                # Return the reference to this proxy object. Returning r would
                # return the untraced reference.
                return self
            else:
                # r is a different connection object.
                # This should not happen in practice but play it safe so that
                # the original functionality is maintained.
                return r
        elif hasattr(r, "execute"):
            # r is Cursor-like.
            if iswrapped(r):
                return r
            return self._self_cursor_cls(r, cfg=self._self_config, db_tags=self._self_db_tags)
        else:
            # Otherwise r is some other object, so maintain the functionality
            # of the original.
            return r

    def _trace_method(
        self, method: Callable[..., Any], name: str, extra_tags: dict[str, Any], *args: Any, **kwargs: Any
    ) -> Any:
        if not is_tracing_enabled():
            return method(*args, **kwargs)

        with core.context_with_event(self._trace_event(name, extra_tags)):
            return method(*args, **kwargs)

    def _trace_event(self, name: str, extra_tags: dict[str, Any]) -> DatabaseOperationEvent:
        return DatabaseOperationEvent(
            component=self._self_config.integration_name,
            integration_config=self._self_config,
            operation_name=name,
            service=ext_service(None, self._self_config),
            measured=False,
            metadata_before_tags=True,
            span_type=None,
            connection_tags=self._self_db_tags,
            tags=extra_tags,
        )

    def cursor(self, *args, **kwargs):
        cursor = self.__wrapped__.cursor(*args, **kwargs)
        return self._self_cursor_cls(cursor=cursor, cfg=self._self_config, db_tags=self._self_db_tags)

    def commit(self, *args, **kwargs):
        span_name = "{}.{}".format(self._self_datadog_name, "commit")
        return self._trace_method(self.__wrapped__.commit, span_name, {}, *args, **kwargs)

    def rollback(self, *args, **kwargs):
        span_name = "{}.{}".format(self._self_datadog_name, "rollback")
        return self._trace_method(self.__wrapped__.rollback, span_name, {}, *args, **kwargs)


def _get_vendor(conn):
    """Return the vendor (e.g postgres, mysql) of the given
    database.
    """
    try:
        name = _get_module_name(conn)
    except Exception:
        log.debug("couldn't parse module name", exc_info=True)
        name = "sql"
    return sql.normalize_vendor(name)


def _get_module_name(conn):
    return conn.__class__.__module__.split(".")[0]
