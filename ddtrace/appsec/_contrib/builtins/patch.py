import os
from typing import Any
from typing import Callable
from typing import TypeVar
from typing import cast

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._asm_request_context import in_asm_context
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._metrics import report_rasp_skipped
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.appsec._rasp import must_block
from ddtrace.appsec._rasp import raise_without_wrapper_frame
from ddtrace.internal._exceptions import BlockingException


T = TypeVar("T")


def patch() -> None:
    try_wrap_function_wrapper("builtins", "open", wrapped_open)


def unpatch() -> None:
    try_unwrap("builtins", "open")


def wrapped_open(original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
    if get_rasp_capability("lfi"):
        filename_arg = args[0] if args else kwargs.get("file")
        try:
            filename = os.fspath(cast(Any, filename_arg))
        except Exception:
            filename = ""
        if filename:
            if in_asm_context():
                result = call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.LFI: filename},
                    crop_trace=wrapped_open.__name__,
                    rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
                )
                if result and must_block(result.actions):
                    raise BlockingException(
                        get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.LFI, filename
                    )
            else:
                report_rasp_skipped(EXPLOIT_PREVENTION.TYPE.LFI, False)
    try:
        return original(*args, **kwargs)
    except Exception as error:
        raise_without_wrapper_frame(error)
