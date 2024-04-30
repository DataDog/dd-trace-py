# This module must not import other modules inconditionnaly that
# require iast, ddwaf or any native optional module.

import ctypes
import gc
import os
from typing import Any
from typing import Callable
from typing import Dict

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config
from ddtrace.vendor.wrapt import FunctionWrapper
from ddtrace.vendor.wrapt import resolve_path


log = get_logger(__name__)
_DD_ORIGINAL_ATTRIBUTES: Dict[Any, Any] = {}


def patch_common_modules():
    try_wrap_function_wrapper("builtins", "open", wrapped_open_CFDDB7ABBA9081B6)

    # due to incompatibilities with gevent, delay the patching if IAST is enabled
    if asm_config._iast_enabled:
        core.on(
            "exploit.prevention.ssrf.patch.urllib",
            lambda: try_wrap_function_wrapper("urllib.request", "OpenerDirector.open", wrapped_open_ED4CF71136E15EBF),
        )
    else:
        try_wrap_function_wrapper("urllib.request", "OpenerDirector.open", wrapped_open_ED4CF71136E15EBF)


def unpatch_common_modules():
    try_unwrap("builtins", "open")
    try_unwrap("urllib.request", "OpenerDirector.open")


def wrapped_open_CFDDB7ABBA9081B6(original_open_callable, instance, args, kwargs):
    """
    wrapper for open file function
    """
    if asm_config._iast_enabled:
        # LFI sink to be added
        pass

    if asm_config._asm_enabled and asm_config._ep_enabled:
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_context
            from ddtrace.appsec._constants import EXPLOIT_PREVENTION
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            return original_open_callable(*args, **kwargs)

        filename_arg = args[0] if args else kwargs.get("file", None)
        try:
            filename = os.fspath(filename_arg)
        except Exception:
            filename = ""
        if filename and in_context():
            call_waf_callback(
                {EXPLOIT_PREVENTION.ADDRESS.LFI: filename},
                crop_trace="wrapped_open_CFDDB7ABBA9081B6",
                rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
            )
            # DEV: Next part of the exploit prevention feature: add block here
    return original_open_callable(*args, **kwargs)


def wrapped_open_ED4CF71136E15EBF(original_open_callable, instance, args, kwargs):
    """
    wrapper for open url function
    """
    if asm_config._iast_enabled:
        # TODO: IAST SSRF sink to be added
        pass

    if asm_config._asm_enabled and asm_config._ep_enabled:
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_context
            from ddtrace.appsec._constants import EXPLOIT_PREVENTION
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            return original_open_callable(*args, **kwargs)

        url = args[0] if args else kwargs.get("fullurl", None)
        if url and in_context():
            if url.__class__.__name__ == "Request":
                url = url.get_full_url()
            if isinstance(url, str):
                call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.SSRF: url},
                    crop_trace="wrapped_open_ED4CF71136E15EBF",
                    rule_type=EXPLOIT_PREVENTION.TYPE.SSRF,
                )
            # DEV: Next part of the exploit prevention feature: add block here
    return original_open_callable(*args, **kwargs)


def wrapped_request_D8CB81E472AF98A2(original_request_callable, instance, args, kwargs):
    """
    wrapper for third party requests.request function
    https://requests.readthedocs.io
    """
    if asm_config._iast_enabled:
        from ddtrace.appsec._iast.taint_sinks.ssrf import _iast_report_ssrf

        _iast_report_ssrf(original_request_callable, *args, **kwargs)
    if asm_config._asm_enabled and asm_config._ep_enabled:
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_context
            from ddtrace.appsec._constants import EXPLOIT_PREVENTION
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            return original_request_callable(*args, **kwargs)

        url = args[1] if len(args) > 1 else kwargs.get("url", None)
        if url and in_context():
            if isinstance(url, str):
                call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.SSRF: url},
                    crop_trace="wrapped_request_D8CB81E472AF98A2",
                    rule_type=EXPLOIT_PREVENTION.TYPE.SSRF,
                )
            # DEV: Next part of the exploit prevention feature: add block here
    return original_request_callable(*args, **kwargs)


def try_unwrap(module, name):
    try:
        (parent, attribute, _) = resolve_path(module, name)
        if (parent, attribute) in _DD_ORIGINAL_ATTRIBUTES:
            original = _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
            apply_patch(parent, attribute, original)
            del _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
    except ModuleNotFoundError:
        pass


def try_wrap_function_wrapper(module: str, name: str, wrapper: Callable) -> None:
    try:
        wrap_object(module, name, FunctionWrapper, (wrapper,))
    except (ImportError, AttributeError):
        log.debug("ASM patching. Module %s.%s does not exist", module, name)


def wrap_object(module, name, factory, args=(), kwargs=None):
    if kwargs is None:
        kwargs = {}
    (parent, attribute, original) = resolve_path(module, name)
    wrapper = factory(original, *args, **kwargs)
    apply_patch(parent, attribute, wrapper)
    return wrapper


def apply_patch(parent, attribute, replacement):
    try:
        current_attribute = getattr(parent, attribute)
        # Avoid overwriting the original function if we call this twice
        if not isinstance(current_attribute, FunctionWrapper):
            _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)] = current_attribute
        setattr(parent, attribute, replacement)
    except (TypeError, AttributeError):
        patch_builtins(parent, attribute, replacement)


def patchable_builtin(klass):
    refs = gc.get_referents(klass.__dict__)
    return refs[0]


def patch_builtins(klass, attr, value):
    """Based on forbiddenfruit package:
    https://github.com/clarete/forbiddenfruit/blob/master/forbiddenfruit/__init__.py#L421
    ---
    Patch a built-in `klass` with `attr` set to `value`

    This function monkey-patches the built-in python object `attr` adding a new
    attribute to it. You can add any kind of argument to the `class`.

    It's possible to attach methods as class methods, just do the following:

      >>> def myclassmethod(cls):
      ...     return cls(1.5)
      >>> curse(float, "myclassmethod", classmethod(myclassmethod))
      >>> float.myclassmethod()
      1.5

    Methods will be automatically bound, so don't forget to add a self
    parameter to them, like this:

      >>> def hello(self):
      ...     return self * 2
      >>> curse(str, "hello", hello)
      >>> "yo".hello()
      "yoyo"
    """
    dikt = patchable_builtin(klass)

    old_value = dikt.get(attr, None)
    old_name = "_c_%s" % attr  # do not use .format here, it breaks py2.{5,6}

    # Patch the thing
    dikt[attr] = value

    if old_value:
        dikt[old_name] = old_value

        try:
            dikt[attr].__name__ = old_value.__name__
        except (AttributeError, TypeError):  # py2.5 will raise `TypeError`
            pass
        try:
            dikt[attr].__qualname__ = old_value.__qualname__
        except AttributeError:
            pass

    ctypes.pythonapi.PyType_Modified(ctypes.py_object(klass))
