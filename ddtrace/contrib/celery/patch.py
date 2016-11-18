# Third party
import celery

# Project
from .app import patch_app, unpatch_app
from .task import patch_task, unpatch_task


def patch():
    """ patch will add all available tracing to the celery library """
    setattr(celery, 'Celery', patch_app(celery.Celery))
    setattr(celery, 'Task', patch_task(celery.Task))


def unpatch():
    """ unpatch will remove tracing from the celery library """
    setattr(celery, 'Celery', unpatch_app(celery.Celery))
    setattr(celery, 'Task', unpatch_task(celery.Task))
