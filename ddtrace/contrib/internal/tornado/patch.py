import os
from typing import Dict

import tornado
from wrapt import wrap_function_wrapper as _w

from ddtrace import config
from ddtrace import tracer
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


DEFAULT_WRAP_EXECUTOR = None


def patch():
    if getattr(tornado, "__datadog_patch", False):
        return
    tornado.__datadog_patch = True
    global DEFAULT_WRAP_EXECUTOR
    DEFAULT_WRAP_EXECUTOR = getattr(tracer, "_wrap_executor", None)
    tracer._wrap_executor = application._wrap_executor

    _w("tornado.web", "Application.__init__", application.tracer_config)
    _w("tornado.web", "RequestHandler._execute", handlers.execute)
    _w("tornado.web", "RequestHandler.on_finish", handlers.on_finish)
    _w("tornado.web", "RequestHandler.log_exception", handlers.log_exception)
    _w("tornado.web", "RequestHandler.flush", handlers._on_flush)

    # patch Template system
    _w("tornado.template", "Template.generate", template.generate)
    # wrapt handles lazy imports, so we can wrap even if tornado.gen isn't imported yet
    try:
        _w("tornado.gen", "sleep", application._wrapped_sleep)
    except (ImportError, AttributeError):
        pass


def unpatch():
    if not getattr(tornado, "__datadog_patch", False):
        return
    tornado.__datadog_patch = False

    tracer._wrap_executor = DEFAULT_WRAP_EXECUTOR

    _u(tornado.web.RequestHandler, "_execute")
    _u(tornado.web.RequestHandler, "on_finish")
    _u(tornado.web.RequestHandler, "log_exception")
    _u(tornado.web.Application, "__init__")
    _u(tornado.template.Template, "generate")
    # Use getattr to avoid creating a local variable that shadows the module-level import
    if getattr(tornado, "gen", None) and hasattr(tornado.gen, "sleep"):
        _u(tornado.gen, "sleep")
