import os


if os.environ.get("DD_PYTEST_USE_NEW_PLUGIN", "").lower() in ("1", "true"):
    from ddtrace.testing.internal.pytest.plugin import *  # noqa: F403
else:
    from ddtrace.contrib.internal.pytest.plugin import *  # noqa: F403
