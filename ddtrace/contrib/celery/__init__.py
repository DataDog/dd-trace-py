"""
The Celery integration will trace all tasks that are executed in the
background. Functions and class based tasks are traced only if the Celery API
is used, so calling the function directly or via the ``run()`` method will not
generate traces. However, calling ``apply()``, ``apply_async()`` and ``delay()``
will produce tracing data. To trace your Celery application, call the patch method::

    import celery
    from ddtrace import patch

    patch(celery=True)
    app = celery.Celery()

    @app.task
    def my_task():
        pass

    class MyTask(app.Task):
        def run(self):
            pass

Configuration
~~~~~~~~~~~~~
.. py:data:: ddtrace.config.celery['distributed_tracing']

   Whether or not to pass distributed tracing headers to Celery workers.
   Note: this flag applies to both Celery workers and callers separately.

   On the caller: enabling propagation causes the caller and worker to
   share a single trace while disabling causes them to be separate.

   On the worker: enabling propagation causes context to propagate across
   tasks, such as when Task A queues work for Task B, or if Task A retries.
   Disabling propagation causes each celery.run task to be in its own
   separate trace.

   Can also be enabled with the ``DD_CELERY_DISTRIBUTED_TRACING`` environment variable.

   Default: ``False``

.. py:data:: ddtrace.config.celery['producer_service_name']

   Sets service name for producer

   Default: ``'celery-producer'``

.. py:data:: ddtrace.config.celery['worker_service_name']

   Sets service name for worker

   Default: ``'celery-worker'``

"""


# Required to allow users to import from  `ddtrace.contrib.celery.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.celery.app import patch_app
from ddtrace.contrib.internal.celery.app import unpatch_app
from ddtrace.contrib.internal.celery.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.celery.patch import patch  # noqa: F401
from ddtrace.contrib.internal.celery.patch import unpatch  # noqa: F401
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate


def __getattr__(name):
    if name in ("patch", "unpatch", "get_version"):
        deprecate(
            ("%s.%s is deprecated" % (__name__, name)),
            message="Use ``import ddtrace.auto`` or the ``ddtrace-run`` command to configure this integration.",
            category=DDTraceDeprecationWarning,
            removal_version="3.0.0",
        )

    if name in globals():
        return globals()[name]
    raise AttributeError("%s has no attribute %s", __name__, name)


__all__ = ["patch_app", "unpatch_app"]
