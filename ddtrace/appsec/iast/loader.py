#!/usr/bin/env python3

from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.module import _ImportHookChainedLoader

from ._ast.ast_patching import _should_iast_patch
from ._ast.ast_patching import astpatch_source
from ._util import _is_iast_enabled


log = get_logger(__name__)


IS_IAST_ENABLED = _is_iast_enabled()


class _IASTLoader(_ImportHookChainedLoader):
    def _exec_module(self, module):
        patched_source = None
        if IS_IAST_ENABLED and _should_iast_patch(module.__name__):
            log.debug("IAST enabled")
            module_path, patched_source = astpatch_source(module.__name__, None)

        if patched_source:
            # Patched source is executed instead of original module
            compiled_code = compile(patched_source, module_path, "exec")
            exec(compiled_code, module.__dict__)
        else:
            self.loader.exec_module(module)

        for callback in self.callbacks.values():
            callback(module)


if IS_IAST_ENABLED:
    ModuleWatchdog.use_loader(_IASTLoader)
