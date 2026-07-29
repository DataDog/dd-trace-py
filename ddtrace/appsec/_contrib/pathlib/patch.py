import os
from types import TracebackType
from typing import Any
from typing import Callable
from typing import NoReturn
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
from ddtrace.internal._exceptions import BlockingException


T = TypeVar("T")


def patch() -> None:
    try_wrap_function_wrapper("pathlib", "Path.open", wrapped_open)


def unpatch() -> None:
    try_unwrap("pathlib", "Path.open")


def _raise_without_wrapper_frame(error: BaseException) -> NoReturn:
    traceback = error.__traceback__
    previous_frame = traceback.tb_frame.f_back if traceback is not None else None
    if previous_frame is None:
        raise error
    raise error.with_traceback(TracebackType(None, previous_frame, previous_frame.f_lasti, previous_frame.f_lineno))


def wrapped_open(original: Callable[..., T], instance: object, args: tuple[Any, ...], kwargs: dict[str, Any]) -> T:
    if get_rasp_capability("lfi"):
        try:
            filename = os.fspath(cast(Any, instance))
        except Exception:
            filename = ""
        if filename:
            if in_asm_context():
                result = call_waf_callback(
                    {EXPLOIT_PREVENTION.ADDRESS.LFI: filename},
                    crop_trace="wrapped_path_open_B91CA5063FE27D84",
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
        _raise_without_wrapper_frame(error)
