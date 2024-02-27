#!/usr/bin/env python3

from ddtrace.internal.logger import get_logger

from ._ast.ast_patching import astpatch_module
from ._utils import _is_iast_enabled


log = get_logger(__name__)


IS_IAST_ENABLED = _is_iast_enabled()


def _exec_iast_patched_module(module_watchdog, module):
    patched_source = None
    compiled_code = None
    if IS_IAST_ENABLED:
        try:
            module_path, patched_source = astpatch_module(module)
        except Exception:
            log.debug("Unexpected exception while AST patching", exc_info=True)
            patched_source = None

    if patched_source:
        try:
            # Patched source is compiled in order to execute it
            compiled_code = compile(patched_source, module_path, "exec")
        except Exception:
            log.debug("Unexpected exception while compiling patched code", exc_info=True)
            compiled_code = None

    if compiled_code:
        # Patched source is executed instead of original module
        exec(compiled_code, module.__dict__)  # nosec B102
    elif module_watchdog.loader is not None:
        module_watchdog.loader.exec_module(module)
