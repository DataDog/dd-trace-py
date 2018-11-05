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


To change Celery service name, you can use the ``Config`` API as follows::

    from ddtrace import config

    # change service names for producers and workers
    config.celery['producer_service_name'] = 'task-queue'
    config.celery['worker_service_name'] = 'worker-notify'

By default, reported service names are:
    * ``celery-producer`` when tasks are enqueued for processing
    * ``celery-worker`` when tasks are processed by a Celery process

"""
from ddtrace import config

from ...utils.install import (
    install_module_import_hook,
    uninstall_module_import_hook,
)
from ...utils.formats import get_env
from .app import patch_app, unpatch_app
from .constants import PRODUCER_SERVICE, WORKER_SERVICE
from .task import patch_task, unpatch_task


__all__ = [
    'patch',
    'patch_app',
    'patch_task',
    'unpatch',
    'unpatch_app',
    'unpatch_task',
]

# Celery default settings
config._add('celery', {
    'producer_service_name': get_env('celery', 'producer_service_name', PRODUCER_SERVICE),
    'worker_service_name': get_env('celery', 'worker_service_name', WORKER_SERVICE),
})


def _patch(celery):
    """Instrument Celery base application and the `TaskRegistry` so
    that any new registered task is automatically instrumented. In the
    case of Django-Celery integration, also the `@shared_task` decorator
    must be instrumented because Django doesn't use the Celery registry.
    """
    patch_app(celery.Celery)


def unpatch():
    """Disconnect all signals and remove Tracing capabilities"""
    import celery
    unpatch_app(celery.Celery)
    uninstall_module_import_hook('celery')


def patch():
    install_module_import_hook('celery', _patch)
