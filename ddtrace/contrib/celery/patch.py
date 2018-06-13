import celery

from wrapt import wrap_function_wrapper as _w

from .app import patch_app, unpatch_app
from .task import _wrap_shared_task
from .registry import _wrap_register
from ...utils.wrappers import unwrap as _u


def patch():
    """Instrument Celery base application and the `TaskRegistry` so
    that any new registered task is automatically instrumented. In the
    case of Django-Celery integration, also the `@shared_task` decorator
    must be instrumented because Django doesn't use the Celery registry.
    """
    setattr(celery, 'Celery', patch_app(celery.Celery))
    _w('celery.app.registry', 'TaskRegistry.register', _wrap_register)
    _w('celery', 'shared_task', _wrap_shared_task)


def unpatch():
    """Removes instrumentation from Celery"""
    setattr(celery, 'Celery', unpatch_app(celery.Celery))
    _u(celery.app.registry.TaskRegistry, 'register')
    _u(celery, 'shared_task')
