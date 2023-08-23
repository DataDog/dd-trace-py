#!/usr/bin/env python3

from ddtrace.internal.logger import get_logger

from ._ast.ast_patching import astpatch_module
from ._utils import _is_iast_enabled


log = get_logger(__name__)


IS_IAST_ENABLED = _is_iast_enabled()


def _exec_iast_patched_module(module_watchdog, module):
    patched_source = None
    if IS_IAST_ENABLED:
        log.debug("IAST enabled")
        try:
            module_path, patched_source = astpatch_module(module)
        except Exception:
            log.debug("Unexpected exception while AST patching", exc_info=True)
            patched_source = None

    if patched_source:
        # Patched source is executed instead of original module
        compiled_code = compile(patched_source, module_path, "exec")
        exec(compiled_code, module.__dict__)  # nosec B102
    else:
        module_watchdog.loader.exec_module(module)
