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
        "wsgi",
        "trace_utils",
        "internal",
    ):
        # following packages/modules are not deprecated and will not be removed in 3.0
        pass
    elif name in ("trace_handlers", "func_name", "module_name", "require_modules"):
        # the following attributes are exposed in ddtrace.contrib.__init__ and should be
        # removed in v3.0
        deprecate(
            ("ddtrace.contrib.%s is deprecated" % name),
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )
    elif name in ("aiobotocore", "httplib", "kombu", "snowflake", "sqlalchemy", "tornado", "urllib3"):
        # following integrations are not enabled by default and require a unique deprecation message
        deprecate(
            f"ddtrace.contrib.{name} is deprecated",
            message="Avoid using this package directly. "
            f"Set DD_TRACE_{name.upper()}_ENABLED=true and use ``ddtrace.auto`` or the "
            "``ddtrace-run`` command to enable and configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )
    elif name in ("redis_utils", "trace_utils_redis", "trace_utils_async"):
        deprecate(
            f"The ddtrace.contrib.{name} module is deprecated",
            message="Import from ``ddtrace.contrib.trace_utils`` instead.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )
    elif name == "flask_login":
        deprecate(
            """The flask_login integration is deprecated and will be deleted.
        We recommend customers to switch to manual instrumentation.
        https://docs.datadoghq.com/security/application_security/threats/add-user-info/?tab=loginsuccess&code-lang=python#adding-business-logic-information-login-success-login-failure-any-business-logic-to-traces
        """,
            message="",
            category=DDTraceDeprecationWarning,
        )
    else:
        deprecate(
            f"ddtrace.contrib.{name} is deprecated",
            message="Avoid using this package directly. "
            f"Use ``import ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure {name}.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)
