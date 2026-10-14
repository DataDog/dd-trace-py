from ddtrace_base.settings.env import dd_environ as env


# NOTE: Changed default value to true
if env.get("DD_PYTEST_USE_NEW_PLUGIN", "").lower() in ("0", "false"):
    from ddtrace.contrib.internal.pytest.plugin import *  # noqa: F403
else:
    from ddtrace_testing.internal.pytest.plugin import *  # noqa: F403
