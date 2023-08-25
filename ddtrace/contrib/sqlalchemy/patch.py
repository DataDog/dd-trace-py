import sqlalchemy

from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ..trace_utils import unwrap
from .engine import _wrap_create_engine


def get_version():
    # type: () -> str
    return getattr(sqlalchemy, "__version__", "")


def patch():
    if getattr(sqlalchemy.engine, "__datadog_patch", False):
        return
    sqlalchemy.engine.__datadog_patch = True

    # patch the engine creation function
    _w("sqlalchemy", "create_engine", _wrap_create_engine)
    _w("sqlalchemy.engine", "create_engine", _wrap_create_engine)


def unpatch():
    # unpatch sqlalchemy
    if getattr(sqlalchemy.engine, "__datadog_patch", False):
        sqlalchemy.engine.__datadog_patch = False
        unwrap(sqlalchemy, "create_engine")
        unwrap(sqlalchemy.engine, "create_engine")
