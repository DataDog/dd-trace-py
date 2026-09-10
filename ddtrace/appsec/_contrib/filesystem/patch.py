from contextlib import suppress
import os
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace.appsec._asm_request_context import call_waf_callback
from ddtrace.appsec._asm_request_context import get_blocked
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._patch_utils import try_unwrap
from ddtrace.appsec._patch_utils import try_wrap_function_wrapper
from ddtrace.appsec._rasp import _must_block
from ddtrace.appsec._rasp import get_rasp_capability
from ddtrace.internal._exceptions import BlockingException


class _OpenCallable(Protocol):
    def __call__(self, *args: object, **kwargs: object) -> object: ...


class _PathOpenReceiver(Protocol):
    def __fspath__(self) -> str: ...


def wrapped_builtin_open(
    original: _OpenCallable,
    _instance: object,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object:
    with suppress(Exception):
        if get_rasp_capability("lfi"):
            value = args[0] if args else kwargs.get("file")
            filename: Optional[Union[bytes, str]] = None
            if isinstance(value, (str, bytes, os.PathLike)):
                try:
                    filename = os.fspath(value)
                except Exception:
                    filename = None
            if filename:
                handle_lfi(filename)

    return original(*args, **kwargs)


def wrapped_path_open(
    original: _OpenCallable,
    instance: _PathOpenReceiver,
    args: tuple[object, ...],
    kwargs: dict[str, object],
) -> object:
    with suppress(Exception):
        if get_rasp_capability("lfi"):
            try:
                filename = os.fspath(instance)
            except Exception:
                filename = None
            if filename:
                handle_lfi(filename)

    return original(*args, **kwargs)


def handle_lfi(filename: Union[str, bytes]) -> None:
    result = call_waf_callback(
        {EXPLOIT_PREVENTION.ADDRESS.LFI: filename},
        crop_trace="handle_lfi",
        rule_type=EXPLOIT_PREVENTION.TYPE.LFI,
    )
    if result is None or not _must_block(result.actions):
        return

    raise BlockingException(get_blocked(), EXPLOIT_PREVENTION.BLOCKING, EXPLOIT_PREVENTION.TYPE.LFI, filename)


def patch() -> None:
    try_wrap_function_wrapper("builtins", "open", wrapped_builtin_open)
    try_wrap_function_wrapper("pathlib", "Path.open", wrapped_path_open)


def unpatch() -> None:
    try_unwrap("builtins", "open")
    try_unwrap("pathlib", "Path.open")
