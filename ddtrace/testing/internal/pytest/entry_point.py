from ddtrace.internal.settings import env


# NOTE: Changed default value to true
if env.get("DD_PYTEST_USE_NEW_PLUGIN", "").lower() in ("0", "false"):
    from ddtrace import DDTraceDeprecationWarning
    from ddtrace.vendor.debtcollector import deprecate

    deprecate(
        "DD_PYTEST_USE_NEW_PLUGIN=false is deprecated",
        message=(
            "The legacy pytest plugin (ddtrace/contrib/internal/pytest) is deprecated and will be removed "
            "in dd-trace-py 5.0.0. Remove the DD_PYTEST_USE_NEW_PLUGIN environment variable to use the "
            "new default plugin."
        ),
        removal_version="5.0.0",
        category=DDTraceDeprecationWarning,
    )
    from ddtrace.contrib.internal.pytest.plugin import *  # noqa: F403
else:
    from ddtrace.testing.internal.pytest.plugin import *  # noqa: F403
