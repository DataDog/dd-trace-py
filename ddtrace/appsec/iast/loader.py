#!/usr/bin/env python3

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog

from ._ast.ast_patching import _should_iast_patch
from ._ast.ast_patching import astpatch_source
from ._util import _is_iast_enabled


log = get_logger(__name__)


IS_IAST_ENABLED = _is_iast_enabled()


def _exec_iast_patched_module(module_watchdog, module):
    patched_source = None
    if IS_IAST_ENABLED:
        log.debug("IAST enabled")
        module_path, patched_source = astpatch_source(module.__name__, None)

    if patched_source:
        # Patched source is executed instead of original module
        compiled_code = compile(patched_source, module_path, "exec")
        exec(compiled_code, module.__dict__)
    else:
        module_watchdog.loader.exec_module(module)


if IS_IAST_ENABLED:
    ModuleWatchdog.register_pre_exec_module_hook(_should_iast_patch, _exec_iast_patched_module)
