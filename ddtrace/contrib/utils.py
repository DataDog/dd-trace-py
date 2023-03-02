from typing import Tuple

from ddtrace import Pin
from ddtrace import config
from ddtrace.constants import SPAN_MEASURED_KEY
from ddtrace.contrib import dbapi
from ddtrace.contrib.trace_utils import ext_service
from ddtrace.ext import SpanTypes
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.ext import sql
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor import wrapt

from ..propagation._database_monitoring import default_sql_injector as _default_sql_injector


def psycopg_sql_injector_factory(composable_class, sql_class):
    def _psycopg_sql_injector(dbm_comment, sql_statement):
        if isinstance(sql_statement, composable_class):
            return sql_class(dbm_comment) + sql_statement
        return _default_sql_injector(dbm_comment, sql_statement)

    return _psycopg_sql_injector


class PsycopgTracedCursor(dbapi.TracedCursor):
    """TracedCursor for psycopg instances"""

    def __init__(self, cursor, pin, cfg, connection=None):
        if isinstance(cfg, Tuple):
            connection = cfg[0]
        if connection and not pin:
            pin = Pin.get_from(connection)
            cfg = pin._config
            cursor = cursor(connection)
        super(PsycopgTracedCursor, self).__init__(cursor, pin, cfg)

    def _trace_method(self, method, name, resource, extra_tags, dbm_propagator, *args, **kwargs):
        # treat Composable resource objects as strings
        if resource.__class__.__name__ == "SQL" or resource.__class__.__name__ == "Composed":
            resource = resource.as_string(self.__wrapped__)
        return super(PsycopgTracedCursor, self)._trace_method(
            method, name, resource, extra_tags, dbm_propagator, *args, **kwargs
        )


class PsycopgFetchTracedCursor(PsycopgTracedCursor, dbapi.FetchTracedCursor):
    """FetchTracedCursor for psycopg"""


class PsycopgTracedConnection(dbapi.TracedConnection):
    """TracedConnection wraps a Connection with tracing code."""

    def __init__(self, conn, pin=None, cursor_cls=None):
        package = conn.__class__.__module__.split(".")[0]
        if not cursor_cls:
            # Do not trace `fetch*` methods by default
            cursor_cls = (
                PsycopgFetchTracedCursor if config._config[package].trace_fetch_methods else PsycopgTracedCursor
            )

        super(PsycopgTracedConnection, self).__init__(conn, pin, config._config[package], cursor_cls=cursor_cls)

    def execute(self, *args, **kwargs):
        """Execute a query and return a cursor to read its results."""
        span_name = "{}.{}".format(self._self_datadog_name, "execute")

        def patched_execute(*args, **kwargs):
            try:
                cur = self.cursor()
                if kwargs.get("binary", None):
                    cur.format = 1  # set to 1 for binary or 0 if not
                return cur.execute(*args, **kwargs)
            except Exception as ex:
                raise ex.with_traceback(None)

        return self._trace_method(patched_execute, span_name, {}, *args, **kwargs)


def patch_conn(conn, traced_conn_cls=PsycopgTracedConnection, pin=None):
    """Wrap will patch the instance so that its queries are traced."""
    # ensure we've patched extensions (this is idempotent) in
    # case we're only tracing some connections.
    _config = None
    if pin:
        extensions_to_patch = pin._config.get("_extensions_to_patch", None)
        _config = pin._config
        if extensions_to_patch:
            _patch_extensions(extensions_to_patch)

    c = traced_conn_cls(conn)

    # if the connection has an info attr, we are using psycopg3
    if hasattr(conn, "dsn"):
        dsn = sql.parse_pg_dsn(conn.dsn)
    else:
        dsn = sql.parse_pg_dsn(conn.info.dsn)

    tags = {
        net.TARGET_HOST: dsn.get("host"),
        net.TARGET_PORT: dsn.get("port", 5432),
        db.NAME: dsn.get("dbname"),
        db.USER: dsn.get("user"),
        "db.application": dsn.get("application_name"),
        db.SYSTEM: "postgresql",
    }
    Pin(tags=tags, _config=_config).onto(c)
    return c


def _patch_extensions(_extensions):
    # we must patch extensions all the time (it's pretty harmless) so split
    # from global patching of connections. must be idempotent.
    for _, module, func, wrapper in _extensions:
        if not hasattr(module, func) or isinstance(getattr(module, func), wrapt.ObjectProxy):
            continue
        wrapt.wrap_function_wrapper(module, func, wrapper)


def _unpatch_extensions(_extensions):
    # we must patch extensions all the time (it's pretty harmless) so split
    # from global patching of connections. must be idempotent.
    for original, module, func, _ in _extensions:
        setattr(module, func, original)


#
# monkeypatch targets
#


def patched_connect(connect_func, _, args, kwargs):
    _config = globals()["config"]._config
    module_name = (
        connect_func.__module__
        if len(connect_func.__module__.split(".")) == 1
        else connect_func.__module__.split(".")[0]
    )
    pin = Pin.get_from(_config[module_name].base_module)

    if not pin or not pin.enabled() or not pin._config.trace_connect:
        conn = connect_func(*args, **kwargs)
    else:
        with pin.tracer.trace(
            "{}.{}".format(connect_func.__module__, connect_func.__name__),
            service=ext_service(pin, pin._config),
            span_type=SpanTypes.SQL,
        ) as span:
            span.set_tag_str(COMPONENT, pin._config.integration_name)
            if span.get_tag(db.SYSTEM) is None:
                span.set_tag_str(db.SYSTEM, pin._config.dbms_name)

            span.set_tag(SPAN_MEASURED_KEY)
            conn = connect_func(*args, **kwargs)

    return patch_conn(conn, pin=pin)
