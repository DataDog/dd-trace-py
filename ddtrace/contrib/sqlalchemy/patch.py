import sqlalchemy

from ddtrace.appsec._iast._metrics import _set_metric_iast_instrumented_sink
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.settings.asm import config as asm_config
from ddtrace.vendor.debtcollector import deprecate
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w

from ..trace_utils import unwrap
from .engine import _wrap_create_engine


def _get_version():
    # type: () -> str
    return getattr(sqlalchemy, "__version__", "")


def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()


def patch():
    if getattr(sqlalchemy.engine, "__datadog_patch", False):
        return
    sqlalchemy.engine.__datadog_patch = True

    # patch the engine creation function
    _w("sqlalchemy", "create_engine", _wrap_create_engine)
    _w("sqlalchemy.engine", "create_engine", _wrap_create_engine)

    if asm_config._iast_enabled:
        _set_metric_iast_instrumented_sink(VULN_SQL_INJECTION)


def unpatch():
    # unpatch sqlalchemy
    if getattr(sqlalchemy.engine, "__datadog_patch", False):
        sqlalchemy.engine.__datadog_patch = False
        unwrap(sqlalchemy, "create_engine")
        unwrap(sqlalchemy.engine, "create_engine")
