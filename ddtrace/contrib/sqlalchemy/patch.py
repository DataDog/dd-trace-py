from sqlalchemy import engine

from wrapt import wrap_function_wrapper as _w
from ddtrace.util import unwrap

from .engine import _wrap_create_engine


def patch():
    if getattr(engine, '__datadog_patch', False):
        return
    setattr(engine, '__datadog_patch', True)

    # patch the engine creation function
    _w('sqlalchemy.engine', 'create_engine', _wrap_create_engine)


def unpatch():
    # unpatch sqlalchemy
    if getattr(engine, '__datadog_patch', False):
        setattr(engine, '__datadog_patch', False)
        unwrap(engine, 'create_engine')
