from ddtrace import Pin
from ddtrace.pin import _DD_PIN_NAME
from ddtrace.ext import AppTypes

from .util import APP, WORKER_SERVICE


def patch_app(app, pin=None):
    """Attach the Pin class to the application"""
    pin = pin or Pin(service=WORKER_SERVICE, app=APP, app_type=AppTypes.worker)
    pin.onto(app)
    return app


def unpatch_app(app):
    """ unpatch_app will remove tracing from a celery app """
    pin = Pin.get_from(app)
    if pin is not None:
        delattr(app, _DD_PIN_NAME)
