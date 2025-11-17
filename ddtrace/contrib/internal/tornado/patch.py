import os
from typing import Dict

import tornado
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace.internal.utils.formats import asbool
from ddtrace.internal.utils.wrappers import unwrap as _u

from . import application
from . import handlers
from . import template


config._add(
    "tornado",
    dict(
        distributed_tracing=asbool(os.getenv("DD_TORNADO_DISTRIBUTED_TRACING", default=True)),
    ),
)


def get_version():
    # type: () -> str
    return getattr(tornado, "version", "0.0.0")


def _supported_versions() -> Dict[str, str]:
    return {"tornado": ">=6.1"}


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
