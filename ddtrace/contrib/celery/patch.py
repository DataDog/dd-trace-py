import celery

from ddtrace import config
import ddtrace.internal.forksafe as forksafe

from ...internal.utils.formats import get_env
from .app import patch_app
from .app import unpatch_app
from .constants import PRODUCER_SERVICE
from .constants import WORKER_SERVICE


forksafe._soft = True


# Celery default settings
config._add(
    "celery",
    {
        "distributed_tracing": get_env("celery", "distributed_tracing", default=False),
        "producer_service_name": get_env("celery", "producer_service_name", default=PRODUCER_SERVICE),
        "worker_service_name": get_env("celery", "worker_service_name", default=WORKER_SERVICE),
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
