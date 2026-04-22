import mariadb
import wrapt

from ddtrace import config
from ddtrace.contrib.dbapi import TracedConnection
from ddtrace.contrib.internal.trace_utils import _convert_to_string
from ddtrace.ext import db
from ddtrace.ext import net
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap


config._add(
    "mariadb",
    dict(
        trace_fetch_methods=asbool(env.get("DD_MARIADB_TRACE_FETCH_METHODS", default=False)),
        _default_service=schematize_service_name("mariadb"),
        _dbapi_span_name_prefix="mariadb",
    ),
)


def get_version() -> str:
    return getattr(mariadb, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"mariadb": ">=1.0.0"}


def patch():
    if getattr(mariadb, "_datadog_patch", False):
        return
    mariadb._datadog_patch = True
    wrapt.wrap_function_wrapper("mariadb", "connect", _connect)


def unpatch():
    if getattr(mariadb, "_datadog_patch", False):
        mariadb._datadog_patch = False
        unwrap(mariadb, "connect")


def _connect(func, instance, args, kwargs):
    conn = func(*args, **kwargs)
    return TracedConnection(
        conn,
        cfg=config.mariadb,
        db_tags={
            db.SYSTEM: "mariadb",
            net.TARGET_HOST: _convert_to_string(kwargs.get("host", "127.0.0.1")),
            net.TARGET_PORT: _convert_to_string(kwargs.get("port", 3306)),
            net.SERVER_ADDRESS: _convert_to_string(kwargs.get("host", "127.0.0.1")),
            db.USER: _convert_to_string(kwargs.get("user", "test")),
            db.NAME: _convert_to_string(kwargs.get("database", "test")),
        },
    )
