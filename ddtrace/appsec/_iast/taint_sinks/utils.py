from functools import wraps
from typing import Callable

from ddtrace.settings.asm import config as asm_config


def patch_once(func: Callable) -> Callable:
    """Decorator to handle common patching pattern for taint sinks.

    This decorator handles:
    1. Checking if already patched
    2. Checking if IAST is enabled
    3. Setting the patched flag
    """
    _is_patched = False

    @wraps(func)
    def wrapper(*args, **kwargs):
        nonlocal _is_patched
        if _is_patched and not asm_config._iast_is_testing:
            return

        if not asm_config._iast_enabled:
            return

        result = func(*args, **kwargs)
        _is_patched = True
        return result

    return wrapper
