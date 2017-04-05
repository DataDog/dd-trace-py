import wrapt
import ddtrace

import tornado

from . import handlers, application, decorators
from .stack_context import TracerStackContext
from ...util import unwrap


def patch():
    """
    Tracing function that patches the Tornado web application so that it will be
    traced using the given ``tracer``.
    """
    # patch only once
    if getattr(tornado, '__datadog_patch', False):
        return
    setattr(tornado, '__datadog_patch', True)

    # patch all classes and functions
    _w = wrapt.wrap_function_wrapper
    _w('tornado.web', 'RequestHandler._execute', handlers.execute)
    _w('tornado.web', 'RequestHandler.on_finish', handlers.on_finish)
    _w('tornado.web', 'RequestHandler.log_exception', handlers.log_exception)
    _w('tornado.web', 'Application.__init__', application.tracer_config)
    _w('tornado.concurrent', 'run_on_executor', decorators._run_on_executor)

    # configure the global tracer
    ddtrace.tracer.configure(
        context_provider=TracerStackContext.current_context,
        wrap_executor=decorators.wrap_executor,
    )


def unpatch():
    """
    Remove all tracing functions in a Tornado web application.
    """
    if not getattr(tornado, '__datadog_patch', False):
        return
    setattr(tornado, '__datadog_patch', False)

    # unpatch all classes and functions
    unwrap(tornado.web.RequestHandler, '_execute')
    unwrap(tornado.web.RequestHandler, 'on_finish')
    unwrap(tornado.web.RequestHandler, 'log_exception')
    unwrap(tornado.web.Application, '__init__')
    unwrap(tornado.concurrent, 'run_on_executor')
