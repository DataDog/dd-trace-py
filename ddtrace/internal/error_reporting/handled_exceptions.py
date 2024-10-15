import sys
import traceback
import ddtrace

from types import ModuleType
from ddtrace.internal.module import BaseModuleWatchdog

inject_handled_exception_reporting = None

# Import are noqa'd otherwise some formatters will helpfully remove them
if sys.version_info >= (3, 11):  # and sys.version_info < (3, 12):
    from ddtrace.internal.error_reporting.handled_exceptions_py3_11 import _inject_handled_exception_reporting  # noqa
    inject_handled_exception_reporting = _inject_handled_exception_reporting

ENABLED_PACKAGES = ['mypack', 'django', 'saleor']


class HandledExceptionReportingWatchdog(BaseModuleWatchdog):
    def after_import(self, module: ModuleType):
        if not module.__name__:
            return
        print('analyzing', module.__name__)
        for package in ENABLED_PACKAGES:
            if module.__name__.startswith(package):
                print('----------> yes')
                instrument_module(module.__name__)


def instrument_module(module_name: str):
    if inject_handled_exception_reporting is None:
        return

    mod = sys.modules[module_name]
    names = dir(mod)
    for name in names:
        obj = mod.__dict__[name]
        if hasattr(obj, '__code__'):
            # simple functions
            inject_handled_exception_reporting(obj)
        elif isinstance(obj, type):
            _instrument_class(obj)


def _instrument_class(cls: type):
    if inject_handled_exception_reporting is None:
        return

    # methods in classes
    for potential_method_name in dir(cls):
        try:
            potential_method = getattr(cls, potential_method_name)
            if type(potential_method) is type:
                _instrument_class(potential_method)
            elif hasattr(potential_method, '__code__'):
                inject_handled_exception_reporting(potential_method)
        except:
            pass


def _default_datadog_exc_callback():
    print('exception....')
    exc = sys.exception()
    if not exc:
        return

    span = ddtrace.tracer.current_span()
    if not span:
        return

    span._add_event("handled_exception", {"message": str(
        exc), "type": type(exc).__name__, "stack": ''.join(traceback.format_exception(exc))})
