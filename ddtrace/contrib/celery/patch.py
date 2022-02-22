import os

import celery

from ddtrace import config
import ddtrace.internal.forksafe as forksafe
from ddtrace.internal.utils.formats import asbool

from .app import patch_app
from .app import unpatch_app
from .constants import PRODUCER_SERVICE
from .constants import WORKER_SERVICE


forksafe._soft = True


# Celery default settings
config._add(
    "celery",
    {
        "distributed_tracing": asbool(os.getenv("DD_CELERY_DISTRIBUTED_TRACING", default=False)),
        "producer_service_name": os.getenv("DD_CELERY_PRODUCER_SERVICE_NAME", default=PRODUCER_SERVICE),
        "worker_service_name": os.getenv("DD_CELERY_WORKER_SERVICE_NAME", default=WORKER_SERVICE),
    },
)


def patch():
    """Instrument Celery base application and the `TaskRegistry` so
    that any new registered task is automatically instrumented. In the
    case of Django-Celery integration, also the `@shared_task` decorator
    must be instrumented because Django doesn't use the Celery registry.
    """
    patch_app(celery.Celery)


def unpatch():
    """Disconnect all signals and remove Tracing capabilities"""
    unpatch_app(celery.Celery)
