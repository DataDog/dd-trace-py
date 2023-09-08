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

   Can also be enabled with the ``DD_CELERY_DISTRIBUTED_TRACING`` environment variable.

   Default: ``False``

.. py:data:: ddtrace.config.celery['producer_service_name']

   Sets service name for producer

   Default: ``'celery-producer'``

.. py:data:: ddtrace.config.celery['worker_service_name']

   Sets service name for worker

   Default: ``'celery-worker'``

"""
from ...internal.utils.importlib import require_modules


required_modules = ["celery"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .app import patch_app
        from .app import unpatch_app
        from .patch import get_version
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "patch_app", "unpatch", "unpatch_app", "get_version"]
