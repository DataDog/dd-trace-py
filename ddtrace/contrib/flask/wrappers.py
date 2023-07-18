from ddtrace import config
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.vendor.wrapt import function_wrapper

from .. import trace_utils
from ...internal.logger import get_logger
from ...internal.utils.importlib import func_name
from ...pin import Pin
from .helpers import get_current_app


log = get_logger(__name__)


def _wrap_call(func, instance, name, resource=None, signal=None, do_dispatch=False):
    @function_wrapper
    def patch_func(wrapped, _instance, args, kwargs):
        pin = Pin._find(wrapped, _instance, instance, get_current_app())
        if not pin or not pin.enabled():
            return wrapped(*args, **kwargs)
        with core.context_with_data(
            "flask.call", name=name, pin=pin, flask_config=config.flask, resource=resource, signal=signal
        ) as ctx, ctx.get_item("flask_call"):
            if do_dispatch:
                results, exceptions = core.dispatch("flask.wrapped_view", [kwargs])
                if results and results[0]:
                    callback_block, _kwargs = results[0]
                    if callback_block:
                        return callback_block()
                    if _kwargs:
                        for k in kwargs:
                            kwargs[k] = _kwargs[k]
            return wrapped(*args, **kwargs)

    return patch_func(func)


def wrap_view(instance, func, name=None, resource=None):
    return _wrap_call(func, instance, name or func_name(func), resource=resource, do_dispatch=True)


def wrap_function(instance, func, name=None, resource=None):
    return _wrap_call(func, instance, name or func_name(func), resource=resource)


def wrap_signal(app, signal, func):
    return _wrap_call(func, app, func_name(func), signal=signal)
