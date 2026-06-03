import logging
from types import FunctionType
from types import ModuleType
from typing import Any
from typing import Optional
from typing import cast

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib import dbapi
from ddtrace.contrib.internal.trace_utils import _convert_to_string
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.ext import sql as sqlx
from ddtrace.internal.compat import is_wrapted
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.utils.cache import cached
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import wrap
from ddtrace.propagation._database_monitoring import _DBM_Propagator


log = get_logger(__name__)


# PERF: cache the getattr lookup for the Django config
config_django: IntegrationConfig = cast(IntegrationConfig, config.django)


DB_CONN_ATTR_BY_TAG = {
    net.TARGET_HOST: "HOST",
    net.TARGET_PORT: "PORT",
    net.SERVER_ADDRESS: "HOST",
    db.USER: "USER",
    db.NAME: "NAME",
}


@cached()
def get_traced_cursor_cls(cursor_type: type[Any]) -> type[dbapi.TracedCursor]:
    traced_cursor_cls = dbapi.TracedCursor
    try:
        if cursor_type.__module__.startswith("psycopg2.") or cursor_type.__name__ == "Psycopg2TracedCursor":
            # Import lazily to avoid importing psycopg if not already imported.
            from ddtrace.contrib.internal.psycopg.cursor import Psycopg2TracedCursor

            traced_cursor_cls = Psycopg2TracedCursor
        elif cursor_type.__module__.startswith("psycopg.") or cursor_type.__name__ == "Psycopg3TracedCursor":
            # Import lazily to avoid importing psycopg if not already imported.
            from ddtrace.contrib.internal.psycopg.cursor import Psycopg3TracedCursor

            traced_cursor_cls = Psycopg3TracedCursor
    except AttributeError:
        pass
    return traced_cursor_cls


def cursor(func: FunctionType, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
    cursor = func(*args, **kwargs)
    instance = args[0]
    vendor = getattr(instance, "vendor", "db")
    alias = getattr(instance, "alias", "default")

    # Don't double wrap Django database cursors:
    #   If the underlying cursor is already wrapped (e.g. by another library),
    #   we just add the Django tags to the existing Pin (if any) and return
    if is_wrapted(cursor.cursor):
        tags = {
            "django.db.vendor": vendor,
            "django.db.alias": alias,
        }

        cursor_tags = getattr(cursor.cursor, "_self_db_tags", None)
        if cursor_tags:
            cursor_tags.update(tags)
        else:
            setattr(cursor.cursor, "_self_db_tags", tags)
        return cursor

    # Always wrap Django database cursors:
    #   If the underlying cursor is not already wrapped,
    #   we wrap the underlying cursor with our TracedCursor class
    #
    #   This allows us to get Database spans for any query executed where we don't
    #   have an integration for the database library in use, or in the case that
    #   the user has disabled the integration for the database library in use.
    pin = Pin.get_from(instance)
    if not pin:
        pin = get_conn_pin(instance)
        pin.onto(instance)

    service = pin.service or get_conn_service_name(alias)
    cfg = get_conn_config(vendor, service)

    traced_cursor_cls = get_traced_cursor_cls(type(cursor.cursor))
    traced_cursor = traced_cursor_cls(
        cursor,
        cfg=cfg,
        db_tags={
            **pin.tags,
            "django.db.vendor": vendor,
            "django.db.alias": alias,
        },
    )

    return traced_cursor


def get_new_connection(func: FunctionType, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
    """Db tags now pass through _self_db_tags and not through pin so we have to instrument
    the creation of the connection.
    """
    instance = args[0]
    db_conn = func(*args, **kwargs)
    conn_tags = getattr(db_conn, "_self_db_tags", None)
    if conn_tags is not None:
        conn_tags.update(
            {
                "django.db.vendor": getattr(instance, "vendor", "db"),
                "django.db.alias": getattr(instance, "alias", "default"),
            }
        )
    return db_conn


@cached()
def get_conn_service_name(alias: str) -> Optional[str]:
    """
    Returns the service name for the given database connection.
    If the service name is not set, it will use the default service name
    from the Django configuration.
    """
    service = config_django.database_service_name
    if not service:
        database_prefix = config_django.database_service_name_prefix
        service = "{}{}{}".format(database_prefix, alias, "db")
        service = schematize_service_name(service)
    return service


@cached()
def get_conn_config(vendor: str, service: Optional[str]) -> IntegrationConfig:
    prefix = sqlx.normalize_vendor(vendor)
    return IntegrationConfig(
        config_django.global_config,
        "django-database",
        _default_service=service,
        _dbapi_span_name_prefix=prefix,
        trace_fetch_methods=config_django.trace_fetch_methods,
        _dbm_propagator=_DBM_Propagator(0, "query"),
    )


def get_conn_pin(conn: Any) -> Pin:
    vendor = getattr(conn, "vendor", "db")
    alias = getattr(conn, "alias", "default")
    tags = {
        "django.db.vendor": vendor,
        "django.db.alias": alias,
    }
    settings_dict = getattr(conn, "settings_dict", {})
    for tag, attr in DB_CONN_ATTR_BY_TAG.items():
        if attr in settings_dict:
            try:
                tags[tag] = _convert_to_string(conn.settings_dict.get(attr))
            except Exception:
                tags[tag] = str(conn.settings_dict.get(attr))

    service = get_conn_service_name(alias)
    pin = Pin(service, tags=tags)
    return pin


def patch_conn(conn: Any) -> Any:
    if not hasattr(conn.__class__, "cursor"):
        log.debug("Connection class %r does not have a cursor method, skipping instrumentation", conn.__class__)
        return conn

    # We want to be sure to pin the instance of the connection, not the base class
    # since multiple connections can have different service names, tags, etc
    pin = get_conn_pin(conn)
    pin.onto(conn)

    # DEV: `conn` is an instance, and so `conn.cursor` is a bound method
    #      we want to wrap the unbound method on the class once
    if not is_wrapped_with(conn.__class__.cursor, cursor):
        wrap(conn.__class__.cursor, cursor)
    if hasattr(conn.__class__, "get_new_connection") and not is_wrapped_with(
        conn.__class__.get_new_connection, get_new_connection
    ):
        wrap(conn.__class__.get_new_connection, get_new_connection)


def get_connection(func: FunctionType, args: tuple[Any], kwargs: dict[str, Any]) -> Any:
    conn = func(*args, **kwargs)
    try:
        patch_conn(conn)
    except Exception:
        if log.isEnabledFor(logging.DEBUG):
            # PERF: repr(conn) can be heavy, only log if we actually need it
            log.debug("Error instrumenting database connection %r", conn, exc_info=True)
    return conn


def instrument_dbs(django: ModuleType) -> None:
    if not is_wrapped_with(django.db.utils.ConnectionHandler.__getitem__, get_connection):
        wrap(
            django.db.utils.ConnectionHandler.__getitem__,
            get_connection,
        )
