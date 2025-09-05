import os

from _ddtrace.internal.utils.formats import asbool


if asbool(os.getenv("DD_CIVISIBILITY_ENABLED", "true")):
    import ddtrace.contrib.internal.pytest.plugin  # noqa:F401
