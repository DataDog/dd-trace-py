from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


deprecate(
    """The flask_login module is deprecated and will be deleted.
We recommend customers to switch to manual instrumentation.
https://docs.datadoghq.com/security/application_security/threats/add-user-info/?tab=loginsuccess&code-lang=python#adding-business-logic-information-login-success-login-failure-any-business-logic-to-traces
""",
    message="",
    category=DDTraceDeprecationWarning,
)


def get_version() -> str:
    deprecate(
        "The flask_login module is deprecated and will be deleted.", message="", category=DDTraceDeprecationWarning
    )
    return ""


def patch():
    deprecate(
        "The flask_login module is deprecated and will be deleted.", message="", category=DDTraceDeprecationWarning
    )


def unpatch():
    deprecate(
        "The flask_login module is deprecated and will be deleted.", message="", category=DDTraceDeprecationWarning
    )
