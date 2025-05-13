# This module must not import other modules unconditionally that require iast
import ctypes
import os
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import Union

from wrapt import FunctionWrapper
from wrapt import resolve_path

from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import WAF_ACTIONS
from ddtrace.appsec._metrics import _report_rasp_skipped
import ddtrace.contrib.internal.subprocess.patch as subprocess_patch
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal._unpatched import _gc as gc
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.settings.asm import config as asm_config


log = get_logger(__name__)
_DD_ORIGINAL_ATTRIBUTES: Dict[Any, Any] = {}

_is_patched = False

_RASP_SYSTEM = "rasp_os.system"
_RASP_POPEN = "rasp_Popen"


def patch_common_modules():
    global _is_patched

    @ModuleWatchdog.after_module_imported("subprocess")
    def _(module):
        # ensure that the subprocess patch is applied even after one click activation
        subprocess_patch.patch()
        subprocess_patch.add_str_callback(_RASP_SYSTEM, wrapped_system_5542593D237084A7)
        subprocess_patch.add_lst_callback(_RASP_POPEN, popen_FD233052260D8B4D)

    if _is_patched:
        return

    try_wrap_function_wrapper("builtins", "open", wrapped_open_CFDDB7ABBA9081B6)
    try_wrap_function_wrapper("urllib.request", "OpenerDirector.open", wrapped_open_ED4CF71136E15EBF)
    core.on("asm.block.dbapi.execute", execute_4C9BAC8E228EB347)
    _is_patched = True


def unpatch_common_modules():
    global _is_patched
    if not _is_patched:
        return
    try_unwrap("builtins", "open")
    try_unwrap("urllib.request", "OpenerDirector.open")
    try_unwrap("_io", "BytesIO.read")
    try_unwrap("_io", "StringIO.read")
    subprocess_patch.del_str_callback(_RASP_SYSTEM)
    subprocess_patch.del_lst_callback(_RASP_POPEN)
    _is_patched = False


def _must_block(actions: Iterable[str]) -> bool:
    return any(action in (WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION) for action in actions)


def wrapped_open_CFDDB7ABBA9081B6(original_open_callable, instance, args, kwargs):
    """
    wrapper for open file function
    """
    if (
        asm_config._asm_enabled
        and asm_config._ep_enabled
        and core.tracer._appsec_processor is not None
        and core.tracer._appsec_processor.rasp_lfi_enabled
    ):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_asm_context
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time

            # DEV: Do not report here for efficiency reasons
            # _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.LFI, True)
            return original_open_callable(*args, **kwargs)

        filename_arg = args[0] if args else kwargs.get("file", None)
        try:
            filename = os.fspath(filename_arg)
        except Exception:
            filename = ""
        if filename:
            if in_asm_context():
                res = call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.LFI: filename},
                    crop_trace="wrapped_open_CFDDB7ABBA9081B6",
                    rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
                )
                if res and _must_block(res.actions):
                    raise BlockingException(
                        get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.LFI, filename
                    )
            else:
                _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.LFI, False)
    try:
        return original_open_callable(*args, **kwargs)
    except Exception as e:
        previous_frame = e.__traceback__.tb_frame.f_back
        raise e.with_traceback(
            e.__traceback__.__class__(None, previous_frame, previous_frame.f_lasti, previous_frame.f_lineno)
        )


def wrapped_open_ED4CF71136E15EBF(original_open_callable, instance, args, kwargs):
    """
    wrapper for open url function
    """
    if asm_config._iast_enabled:
        # TODO: IAST SSRF sink to be added
        pass

    if (
        asm_config._asm_enabled
        and asm_config._ep_enabled
        and core.tracer._appsec_processor is not None
        and core.tracer._appsec_processor.rasp_ssrf_enabled
    ):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_asm_context
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return original_open_callable(*args, **kwargs)

        url = args[0] if args else kwargs.get("fullurl", None)
        if url.__class__.__name__ == "Request":
            url = url.get_full_url()
        if isinstance(url, str) and url:
            if in_asm_context():
                res = call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.SSRF: url},
                    crop_trace="wrapped_open_ED4CF71136E15EBF",
                    rule_type=EXPLOIT_PREVENTION.TYPE.SSRF,
                )
                if res and _must_block(res.actions):
                    raise BlockingException(
                        get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, url
                    )
            else:
                _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
    return original_open_callable(*args, **kwargs)


def wrapped_request_D8CB81E472AF98A2(original_request_callable, instance, args, kwargs):
    """
    wrapper for third party requests.request function
    https://requests.readthedocs.io
    """
    if asm_config._iast_enabled and asm_config.is_iast_request_enabled:
        from ddtrace.appsec._iast.taint_sinks.ssrf import _iast_report_ssrf

        _iast_report_ssrf(original_request_callable, *args, **kwargs)

    if (
        asm_config._asm_enabled
        and asm_config._ep_enabled
        and core.tracer._appsec_processor is not None
        and core.tracer._appsec_processor.rasp_ssrf_enabled
    ):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_asm_context
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return original_request_callable(*args, **kwargs)

        url = args[1] if len(args) > 1 else kwargs.get("url", None)
        if isinstance(url, str) and url:
            if in_asm_context():
                res = call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.SSRF: url},
                    crop_trace="wrapped_request_D8CB81E472AF98A2",
                    rule_type=EXPLOIT_PREVENTION.TYPE.SSRF,
                )
                if res and _must_block(res.actions):
                    raise BlockingException(
                        get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, url
                    )
            else:
                _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
    return original_request_callable(*args, **kwargs)


def wrapped_system_5542593D237084A7(command: str) -> None:
    """
    wrapper for os.system function
    """
    if (
        asm_config._asm_enabled
        and asm_config._ep_enabled
        and core.tracer._appsec_processor is not None  # type: ignore
        and core.tracer._appsec_processor.rasp_shi_enabled  # type: ignore
    ):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_asm_context
        except ImportError:
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SHI, True)
            return

        if in_asm_context():
            res = call_waf_callback(
                {EXPLOIT_PREVENTION.ADDRESS.SHI: command},
                crop_trace="wrapped_system_5542593D237084A7",
                rule_type=EXPLOIT_PREVENTION.TYPE.SHI,
            )
            if res and _must_block(res.actions):
                raise BlockingException(
                    get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SHI, command
                )
        else:
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SHI, False)


def popen_FD233052260D8B4D(arg_list: Union[List[str], str]) -> None:
    """
    listener for subprocess.Popen class
    """
    if (
        asm_config._asm_enabled
        and asm_config._ep_enabled
        and core.tracer._appsec_processor is not None  # type: ignore
        and core.tracer._appsec_processor.rasp_cmdi_enabled  # type: ignore
    ):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_asm_context
        except ImportError:
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.CMDI, True)
            return

        if in_asm_context():
            res = call_waf_callback(
                {EXPLOIT_PREVENTION.ADDRESS.CMDI: arg_list if isinstance(arg_list, list) else [arg_list]},
                crop_trace="popen_FD233052260D8B4D",
                rule_type=EXPLOIT_PREVENTION.TYPE.CMDI,
            )
            if res and _must_block(res.actions):
                raise BlockingException(
                    get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.CMDI, arg_list
                )
        else:
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.CMDI, False)


_DB_DIALECTS = {
    "mariadb": "mariadb",
    "mysql": "mysql",
    "postgres": "postgresql",
    "pymysql": "mysql",
    "pyodbc": "odbc",
    "sql": "sql",
    "sqlite": "sqlite",
    "vertica": "vertica",
}


def execute_4C9BAC8E228EB347(instrument_self, query, args, kwargs) -> None:
    """
    listener for dbapi execute and executemany function
    parameters are ignored as they are properly handled by the dbapi without risk of injections
    """

    if (
        asm_config._asm_enabled
        and asm_config._ep_enabled
        and core.tracer._appsec_processor is not None  # type: ignore
        and core.tracer._appsec_processor.rasp_sqli_enabled  # type: ignore
    ):
        try:
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import in_asm_context
        except ImportError:
            # execute is used during module initialization
            # and shouldn't be changed at that time
            # DEV: Do not report here for efficiency reasons
            # _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SQLI, True)
            return

        if instrument_self and query and isinstance(query, str):
            db_type = _DB_DIALECTS.get(
                getattr(instrument_self, "_self_config", {}).get("_dbapi_span_name_prefix", ""), ""
            )
            if in_asm_context():
                res = call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.SQLI: query, EXPLOIT_PREVENTION.ADDRESS.SQLI_TYPE: db_type},
                    crop_trace="execute_4C9BAC8E228EB347",
                    rule_type=EXPLOIT_PREVENTION.TYPE.SQLI,
                )
                if res and _must_block(res.actions):
                    raise BlockingException(
                        get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SQLI, query
                    )
            else:
                _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SQLI, False)


def try_unwrap(module, name):
    try:
        (parent, attribute, _) = resolve_path(module, name)
        if (parent, attribute) in _DD_ORIGINAL_ATTRIBUTES:
            original = _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
            apply_patch(parent, attribute, original)
            del _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)]
    except ModuleNotFoundError:
        pass


def try_wrap_function_wrapper(module_name: str, name: str, wrapper: Callable) -> None:
    @ModuleWatchdog.after_module_imported(module_name)
    def _(module):
        try:
            wrap_object(module, name, FunctionWrapper, (wrapper,))
        except (ImportError, AttributeError):
            log.debug("ASM patching. Module %s.%s does not exist", module_name, name)


def wrap_object(module, name, factory, args=(), kwargs=None):
    if kwargs is None:
        kwargs = {}
    (parent, attribute, original) = resolve_path(module, name)
    wrapper = factory(original, *args, **kwargs)
    apply_patch(parent, attribute, wrapper)
    wrapper.__deepcopy__ = lambda memo: wrapper
    return wrapper


def apply_patch(parent, attribute, replacement):
    try:
        current_attribute = getattr(parent, attribute)
        # Avoid overwriting the original function if we call this twice
        if not isinstance(current_attribute, FunctionWrapper):
            _DD_ORIGINAL_ATTRIBUTES[(parent, attribute)] = current_attribute
        elif isinstance(replacement, FunctionWrapper) and (
            getattr(replacement, "_self_wrapper", None) is getattr(current_attribute, "_self_wrapper", None)
        ):
            # Avoid double patching
            return
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
