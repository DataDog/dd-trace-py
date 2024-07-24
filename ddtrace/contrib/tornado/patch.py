import os

import tornado

import ddtrace
from ddtrace import config
from ddtrace.vendor.wrapt import wrap_function_wrapper as _w
from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from ...internal.utils.formats import asbool
from ...internal.utils.wrappers import unwrap as _u
from . import application
from . import context_provider
from . import decorators
from . import handlers
from . import template


config._add(
    "tornado",
    dict(
        distributed_tracing=asbool(os.getenv("DD_TORNADO_DISTRIBUTED_TRACING", default=True)),
    ),
)


def _get_version():
    # type: () -> str
    return getattr(tornado, "version", "")

def get_version():
    deprecate(
        "get_version is deprecated",
        message="get_version is deprecated",
        removal_version="3.0.0",
        category=DDTraceDeprecationWarning,
    )
    return _get_version()

def patch():
    """
    Tracing function that patches the Tornado web application so that it will be
    traced using the given ``tracer``.
    """
    # patch only once
    if getattr(tornado, "__datadog_patch", False):
        return
    tornado.__datadog_patch = True

    # patch Application to initialize properly our settings and tracer
    _w("tornado.web", "Application.__init__", application.tracer_config)

    # patch RequestHandler to trace all Tornado handlers
    _w("tornado.web", "RequestHandler._execute", handlers.execute)
    _w("tornado.web", "RequestHandler.on_finish", handlers.on_finish)
    _w("tornado.web", "RequestHandler.log_exception", handlers.log_exception)

    # patch Template system
    _w("tornado.template", "Template.generate", template.generate)

    # configure the global tracer
    ddtrace.tracer.configure(
        context_provider=context_provider,
        wrap_executor=decorators.wrap_executor,
    )


def unpatch():
    """
    Remove all tracing functions in a Tornado web application.
    """
    if not getattr(tornado, "__datadog_patch", False):
        return
    tornado.__datadog_patch = False

    # unpatch Tornado
    _u(tornado.web.RequestHandler, "_execute")
    _u(tornado.web.RequestHandler, "on_finish")
    _u(tornado.web.RequestHandler, "log_exception")
    _u(tornado.web.Application, "__init__")
    _u(tornado.template.Template, "generate")
