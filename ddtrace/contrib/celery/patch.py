# Third party
import celery

# Project
from .app import patch_app, unpatch_app


def patch():
    """ patch will add all available tracing to the celery library """
    setattr(celery, 'Celery', patch_app(celery.Celery))


def unpatch():
    """ unpatch will remove tracing from the celery library """
    setattr(celery, 'Celery', unpatch_app(celery.Celery))
