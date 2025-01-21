from ddtrace._trace import trace_handlers  # noqa:F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.internal.utils.importlib import func_name  # noqa:F401
from ddtrace.internal.utils.importlib import module_name  # noqa:F401
from ddtrace.internal.utils.importlib import require_modules  # noqa:F401
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in (
        "aiohttp",
        "asgi",
        "bottle",
        "celery",
        "cherrypy",
        "falcon",
        "flask_cache",
        "pylibmc",
        "pyramid",
        "requests",
        "sqlalchemy",
        "tornado",
        "wsgi",
        "trace_utils",
        "internal",
    ):
        # following attributes are not deprecated
        pass
    elif name in ("flask_login", "trace_utils_async", "redis_utils", "trace_utils_redis"):
        # folowing integrations/utils have unique deprecation messages
        pass
    elif name in ("trace_handlers", "func_name", "module_name", "require_modules"):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )
    elif name in ("aiobotocore", "httplib", "kombu", "snowflake", "sqlalchemy", "tornado", "urllib3"):
        # following integrations are not enabled by default and require a unique message
        deprecate(
            f"{name} is deprecated",
            message="Avoid using this package directly. "
            f"Set DD_TRACE_{name.upper()}_ENABLED=true and use ``ddtrace.auto`` or the "
            "``ddtrace-run`` command to enable and configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )
    else:
        deprecate(
            f"{name} is deprecated",
            message="Avoid using this package directly. "
            f"Use ``import ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure {name}.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)
