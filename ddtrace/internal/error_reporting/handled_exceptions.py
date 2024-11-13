import sys
import traceback
import types
from types import ModuleType

import ddtrace
from ddtrace.internal.module import BaseModuleWatchdog


inject_handled_exception_reporting = None

# Import are noqa'd otherwise some formatters will helpfully remove them
if sys.version_info >= (3, 11):  # and sys.version_info < (3, 12):
    from ddtrace.internal.error_reporting.handled_exceptions_by_bytecode import _inject_handled_exception_reporting

    inject_handled_exception_reporting = _inject_handled_exception_reporting

ENABLED_PACKAGES = ["django", "saleor", "polls"]
INSTRUMENTABLE_TYPES = (types.FunctionType, types.MethodType, type)


class HandledExceptionReportingWatchdog(BaseModuleWatchdog):
    def after_import(self, module: ModuleType):
        if not module.__name__:
            return
        for package in ENABLED_PACKAGES:
            if module.__name__.startswith(package):
                instrument_module(module.__name__)


def instrument_module(module_name: str):
    if inject_handled_exception_reporting is None:
        return

    mod = sys.modules[module_name]
    names = dir(mod)

    for name in names:
        obj = mod.__dict__[name]
        if type(obj) in INSTRUMENTABLE_TYPES and obj.__module__ == module_name and not name.startswith("__"):
            _instrument_obj(obj)


def _instrument_obj(obj):
    if type(obj) in (types.FunctionType, types.MethodType):
        # functions/methods
        inject_handled_exception_reporting(obj)  # type: ignore
    elif type(obj) is type:
        # classes
        for candidate in dir(obj):
            if type(obj) in (types.FunctionType, types.MethodType, type):
                _instrument_obj(candidate)


def _default_datadog_exc_callback(*args):
    print("I am magically here!!!")
    exc = sys.exception()
    if not exc:
        return

    span = ddtrace.tracer.current_span()
    if not span:
        return

    span._add_event(
        "exception",
        {"message": str(exc), "type": type(exc).__name__, "stack": "".join(traceback.format_exception(exc))},
    )
