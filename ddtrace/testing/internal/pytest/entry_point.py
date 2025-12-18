import os


if os.environ.get("_DD_PYTEST_USE_NEW_PLUGIN", "").lower() in ("1", "true") or os.environ.get(
    "DD_PYTEST_USE_NEW_PLUGIN", ""
).lower() in ("1", "true"):
    from ddtrace.testing.internal.pytest.plugin import *  # noqa: F403

    del os.environ["_DD_PYTEST_USE_NEW_PLUGIN"]
else:
    from ddtrace.contrib.internal.pytest.plugin import *  # noqa: F403
