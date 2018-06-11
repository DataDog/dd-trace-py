import celery

from wrapt import wrap_function_wrapper as _w

from .app import patch_app, unpatch_app
from .registry import _wrap_register
from ...utils.wrappers import unwrap as _u


def patch():
    """Instrument Celery base application and the `TaskRegistry` so
    that any new registered task is automatically instrumented
    """
    setattr(celery, 'Celery', patch_app(celery.Celery))
    _w('celery.app.registry', 'TaskRegistry.register', _wrap_register)


def unpatch():
    """Removes instrumentation from Celery"""
    setattr(celery, 'Celery', unpatch_app(celery.Celery))
    _u(celery.app.registry.TaskRegistry, 'register')
