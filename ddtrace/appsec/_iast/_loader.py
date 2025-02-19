#!/usr/bin/env python3

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
