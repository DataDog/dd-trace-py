from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning

from ...vendor.debtcollector import deprecate
from .app import patch_app


def patch_task(task, pin=None):
    """Deprecated API. The new API uses signals that can be activated via
    patch(celery=True) or through `ddtrace-run` script. Using this API
    enables instrumentation on all tasks.
    """
    deprecate(
        "ddtrace.contrib.celery.patch_task is deprecated",
        message="Use `patch(celery=True)` or `ddtrace-run` script instead",
        category=DDTraceDeprecationWarning,
        removal_version="1.0.0",
    )

    # Enable instrumentation everywhere
    patch_app(task.app)
    return task


def unpatch_task(task):
    """Deprecated API. The new API uses signals that can be deactivated
    via unpatch() API. This API is now a no-op implementation so it doesn't
    affect instrumented tasks.
    """
    deprecate(
        "ddtrace.contrib.celery.patch_task is deprecated",
        message="Use `unpatch()` instead",
        category=DDTraceDeprecationWarning,
        removal_version="1.0.0",
    )
    return task
