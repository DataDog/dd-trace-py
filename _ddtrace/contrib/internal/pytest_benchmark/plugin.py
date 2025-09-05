import os

from _ddtrace.internal.utils.formats import asbool


if asbool(os.getenv("DD_CIVISIBILITY_ENABLED", "true")):
    import ddtrace.contrib.internal.pytest_benchmark.plugin  # noqa:F401
