#!/usr/bin/env python3

import functools
import time

from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ._ast.ast_patching import astpatch_module
from ._taint_tracking._errors import iast_log_error


log = get_logger(__name__)

TOTAL_TIME = 0
LIMIT_TIME = 1.0
FUNC_BLOCKED = False


def execution_limiter():
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            global TOTAL_TIME
            global FUNC_BLOCKED
            if FUNC_BLOCKED:
                log.debug("[IAST] Skipping execution, the time limit has been exceeded.")
                return None

            start_time = time.time()
            result = func(*args, **kwargs)
            elapsed = time.time() - start_time
            TOTAL_TIME += elapsed

            if TOTAL_TIME > LIMIT_TIME:
                from . import disable_iast_propagation

                disable_iast_propagation()
                iast_log_error(f"Execution time exceeded {LIMIT_TIME} seconds. Blocking {func.__name__} permanently.")
                FUNC_BLOCKED = True

            return result

        return wrapper

    return decorator


@execution_limiter()
def _exec_iast_patched_module(module_watchdog, module):
    patched_ast = None
    compiled_code = None
    if asm_config._iast_enabled:
        try:
            module_path, patched_ast = astpatch_module(module)
        except Exception:
            log.debug("Unexpected exception while AST patching", exc_info=True)
            patched_ast = None

    if patched_ast:
        try:
            # Patched source is compiled in order to execute it
            compiled_code = compile(patched_ast, module_path, "exec")
        except Exception:
            log.debug("Unexpected exception while compiling patched code", exc_info=True)
            compiled_code = None

    if compiled_code:
        # Patched source is executed instead of original module
        exec(compiled_code, module.__dict__)  # nosec B102
    elif module_watchdog.loader is not None:
        try:
            module_watchdog.loader.exec_module(module)
        except ImportError:
            log.debug("Unexpected exception on import loader fallback", exc_info=True)
    else:
        log.debug("Module loader is not available, cannot execute module %s", module)
