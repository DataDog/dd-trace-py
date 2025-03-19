#!/usr/bin/env python3

from ddtrace.appsec._iast._logs import iast_compiling_debug_log
from ddtrace.internal.logger import get_logger
from ddtrace.settings.asm import config as asm_config

from ._ast.ast_patching import astpatch_module


log = get_logger(__name__)

IS_IAST_ENABLED = asm_config._iast_enabled


def _exec_iast_patched_module(module_watchdog, module):
    patched_ast = None
    compiled_code = None
    if IS_IAST_ENABLED:
        try:
            module_path, patched_ast = astpatch_module(module)
        except Exception:
            iast_compiling_debug_log("Unexpected exception while AST patching", exc_info=True)
            patched_ast = None

    if patched_ast:
        try:
            # Patched source is compiled in order to execute it
            compiled_code = compile(patched_ast, module_path, "exec")
        except Exception:
            iast_compiling_debug_log("Unexpected exception while compiling patched code", exc_info=True)
            compiled_code = None

    if compiled_code:
        iast_compiling_debug_log(f"INSTRUMENTED CODE. executing {module_path}")
        # Patched source is executed instead of original module
        exec(compiled_code, module.__dict__)  # nosec B102
    elif module_watchdog.loader is not None:
        try:
            iast_compiling_debug_log(f"DEFAULT CODE. executing {module}")
            module_watchdog.loader.exec_module(module)
        except ImportError:
            iast_compiling_debug_log("Unexpected exception on import loader fallback", exc_info=True)
    else:
        iast_compiling_debug_log(f"Module loader is not available, cannot execute module {module}")
