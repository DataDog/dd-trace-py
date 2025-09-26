import ctypes
import io
import json
import os
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from typing import List
from typing import Tuple
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
        log.debug("Patching common modules: subprocess_patch")

    if _is_patched:
        return

    try_wrap_function_wrapper("builtins", "open", wrapped_open_CFDDB7ABBA9081B6)
    try_wrap_function_wrapper("urllib.request", "OpenerDirector.open", wrapped_open_ED4CF71136E15EBF)
    try_wrap_function_wrapper("http.client", "HTTPConnection.request", wrapped_request)
    core.on("asm.block.dbapi.execute", execute_4C9BAC8E228EB347)
    log.debug("Patching common modules: builtins and urllib.request")
    _is_patched = True


def unpatch_common_modules():
    global _is_patched
    if not _is_patched:
        return

    try_unwrap("builtins", "open")
    try_unwrap("urllib.request", "OpenerDirector.open")
    try_unwrap("_io", "BytesIO.read")
    try_unwrap("_io", "StringIO.read")
    subprocess_patch.unpatch()
    subprocess_patch.del_str_callback(_RASP_SYSTEM)
    subprocess_patch.del_lst_callback(_RASP_POPEN)

    log.debug("Unpatching common modules subprocess, builtins and urllib.request")
    _is_patched = False


def _must_block(actions: Iterable[str]) -> bool:
    return any(action in (WAF_ACTIONS.BLOCK_ACTION, WAF_ACTIONS.REDIRECT_ACTION) for action in actions)


def _get_rasp_capability(capability: str) -> bool:
    """Check if the RASP capability is enabled."""
    if asm_config._asm_enabled and asm_config._ep_enabled:
        from ddtrace.appsec._asm_request_context import in_asm_context

        if not in_asm_context():
            return False

        from ddtrace.appsec._processor import AppSecSpanProcessor

        return AppSecSpanProcessor._instance is not None and getattr(
            AppSecSpanProcessor._instance, f"rasp_{capability}_enabled", False
        )
    return False


def wrapped_open_CFDDB7ABBA9081B6(original_open_callable, instance, args, kwargs):
    """
    wrapper for open file function
    """
    if _get_rasp_capability("lfi"):
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


def _build_headers(lst: Iterable[Tuple[str, str]]) -> Dict[str, Union[str, List[str]]]:
    res: Dict[str, Union[str, List[str]]] = {}
    for a, b in lst:
        if a in res:
            v = res[a]
            if isinstance(v, str):
                res[a] = [v, b]
            else:
                v.append(b)
        else:
            res[a] = b
    return res


def wrapped_request(original_request_callable, instance, args, kwargs):
    from ddtrace.appsec._asm_request_context import call_waf_callback

    full_url = core.get_item("full_url")
    if full_url is not None:
        use_body = core.get_item("use_body", False)
        method = args[0] if len(args) > 0 else kwargs.get("method", None)
        body = args[2] if len(args) > 2 else kwargs.get("body", None)
        headers = args[3] if len(args) > 3 else kwargs.get("headers", {})
        addresses = {EXPLOIT_PREVENTION.ADDRESS.SSRF: full_url, "DOWN_REQ_METHOD": method, "DOWN_REQ_HEADERS": headers}
        content_type = headers.get("Content-Type", None) or headers.get("content-type", None)
        if use_body and content_type == "application/json":
            try:
                addresses["DOWN_REQ_BODY"] = json.loads(body)
            except Exception:
                pass  # nosec
        res = call_waf_callback(
            addresses,
            crop_trace="wrapped_open_ED4CF71136E15EBF",
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF,
        )
        if res and _must_block(res.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)
    return original_request_callable(*args, **kwargs)


def _parse_http_response_body(response):
    try:
        if response.length and response.headers.get("content-type", None) == "application/json":
            length = response.length
            body = response.read()
            response.fp = io.BytesIO(body)
            response.length = length
            return json.loads(body)
    except Exception:
        return None
    return None


def wrapped_open_ED4CF71136E15EBF(original_open_callable, instance, args, kwargs):
    """
    wrapper for open url function
    """
    if _get_rasp_capability("ssrf"):
        try:
            from ddtrace.appsec._asm_request_context import _get_asm_context
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import should_analyze_body_response
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return original_open_callable(*args, **kwargs)

        url = args[0] if args else kwargs.get("fullurl", None)
        if url.__class__.__name__ == "Request":
            url = url.get_full_url()
        valid_url = isinstance(url, str) and bool(url)
        if valid_url and url and (ctx := _get_asm_context()):
            use_body = should_analyze_body_response(ctx)
            with core.context_with_data("url_open_analysis", full_url=url, use_body=use_body):
                # API10, doing all request calls in HTTPConnection.request
                try:
                    response = original_open_callable(*args, **kwargs)
                    # api10 response handler for regular responses
                    if response.__class__.__name__ == "HTTPResponse":
                        addresses = {
                            "DOWN_RES_STATUS": str(response.status),
                            "DOWN_RES_HEADERS": _build_headers(response.getheaders()),
                        }
                        if use_body:
                            addresses["DOWN_RES_BODY"] = _parse_http_response_body(response)
                        call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF)
                    return response
                except Exception as e:
                    # api10 response handler for error responses
                    if e.__class__.__name__ == "HTTPError":
                        try:
                            status_code = e.code
                        except Exception:
                            status_code = None
                        try:
                            response_headers = _build_headers(e.headers.items())
                        except Exception:
                            response_headers = None
                        if status_code is not None or response_headers is not None:
                            call_waf_callback(
                                {"DOWN_RES_STATUS": str(status_code), "DOWN_RES_HEADERS": response_headers},
                                rule_type=EXPLOIT_PREVENTION.TYPE.SSRF,
                            )
                    raise
        elif valid_url:
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
    return original_open_callable(*args, **kwargs)


def _parse_headers_urllib3(headers):
    try:
        return dict(headers)
    except Exception:
        return {}


def wrapped_urllib3_make_request(original_request_callable, instance, args, kwargs):
    from ddtrace.appsec._asm_request_context import call_waf_callback

    full_url = core.get_item("full_url")
    if full_url is not None:
        use_body = core.get_item("use_body", False)
        method = args[1] if len(args) > 1 else kwargs.get("method", None)
        body = args[3] if len(args) > 3 else kwargs.get("body", None)
        headers = _parse_headers_urllib3(args[4] if len(args) > 4 else kwargs.get("headers", {}))
        addresses = {EXPLOIT_PREVENTION.ADDRESS.SSRF: full_url, "DOWN_REQ_METHOD": method, "DOWN_REQ_HEADERS": headers}
        content_type = headers.get("Content-Type", None) or headers.get("content-type", None)
        if use_body and content_type == "application/json":
            try:
                addresses["DOWN_REQ_BODY"] = json.loads(body)
            except Exception:
                pass  # nosec
        res = call_waf_callback(
            addresses,
            crop_trace="wrapped_request_D8CB81E472AF98A2",
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF,
        )
        core.discard_item("full_url")
        if res and _must_block(res.actions):
            raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.SSRF, full_url)
    return original_request_callable(*args, **kwargs)


def wrapped_request_D8CB81E472AF98A2(original_request_callable, instance, args, kwargs):
    """
    wrapper for third party requests.request function
    https://requests.readthedocs.io
    """
    if _get_rasp_capability("ssrf"):
        try:
            from ddtrace.appsec._asm_request_context import _get_asm_context
            from ddtrace.appsec._asm_request_context import call_waf_callback
            from ddtrace.appsec._asm_request_context import should_analyze_body_response
        except ImportError:
            # open is used during module initialization
            # and shouldn't be changed at that time
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, True)
            return original_request_callable(*args, **kwargs)

        url = args[1] if len(args) > 1 else kwargs.get("url", None)
        valid_url = isinstance(url, str) and bool(url)
        if valid_url and url and (ctx := _get_asm_context()):
            use_body = should_analyze_body_response(ctx)
            with core.context_with_data("url_open_analysis", full_url=url, use_body=use_body):
                # API10, doing all request calls in HTTPConnection.request
                try:
                    response = original_request_callable(*args, **kwargs)
                    if response.__class__.__name__ == "Response":
                        addresses = {
                            "DOWN_RES_STATUS": str(response.status_code),
                            "DOWN_RES_HEADERS": dict(response.headers),
                        }
                        if use_body:
                            try:
                                addresses["DOWN_RES_BODY"] = response.json()
                            except Exception:
                                pass  # nosec
                        call_waf_callback(addresses, rule_type=EXPLOIT_PREVENTION.TYPE.SSRF)
                    return response
                except Exception:
                    raise
        elif valid_url:
            _report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.SSRF, False)
    return original_request_callable(*args, **kwargs)


def wrapped_system_5542593D237084A7(command: str) -> None:
    """
    wrapper for os.system function
    """
    if _get_rasp_capability("shi"):
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
    if _get_rasp_capability("cmdi"):
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

    if _get_rasp_capability("sqli"):
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
    except (ModuleNotFoundError, AttributeError):
        log.debug("ERROR unwrapping %s.%s ", module, name)


def try_wrap_function_wrapper(module_name: str, name: str, wrapper: Callable) -> None:
    @ModuleWatchdog.after_module_imported(module_name)
    def _(module):
        try:
            wrap_object(module, name, FunctionWrapper, (wrapper,))
        except (ImportError, AttributeError):
            log.debug("Module %s.%s does not exist", module_name, name)


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
