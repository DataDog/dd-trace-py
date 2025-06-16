from typing import Callable
from typing import Set
from typing import Text

from wrapt import FunctionWrapper
from wrapt.importer import when_imported

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper
from ddtrace.appsec._common_module_patches import wrap_object
from ddtrace.appsec._iast._logs import iast_instrumentation_wrapt_debug_log
from ddtrace.settings.asm import config as asm_config


MODULES_TO_UNPATCH: Set["IASTModule"] = set()


class IASTModule:
    name = ""
    function = ""
    hook = ""
    force = False

    def __init__(self, name, function, hook, force=False):
        self.name = name
        self.function = function
        self.hook = hook
        self.force = force

    @staticmethod
    def force_wrapper(module: Text, name: Text, wrapper: Callable):
        try:
            wrap_object(module, name, FunctionWrapper, (wrapper,))
        except (ImportError, AttributeError):
            iast_instrumentation_wrapt_debug_log(f"Module {module}.{name} not exists")

    def wrapt_wrapper(self):
        @when_imported(self.name)
        def _(m):
            self.force_wrapper(m, self.function, self.hook)

    def patch(self):
        if self.force is True:
            self.force_wrapper(self.name, self.function, self.hook)
        else:
            try_wrap_function_wrapper(self.name, self.function, self.hook)
        return True

    def unpatch(self):
        try_unwrap(self.name, self.function)

    def __repr__(self):
        return (
            f"IASTModule(name={self.name}, " f"function={self.function}, " f"hook={self.hook}, " f"force={self.force})"
        )


class WrapModulesForIAST:
    def __init__(self):
        self.modules: Set[IASTModule] = set()
        self.testing: bool = asm_config._iast_is_testing

    def add_module(self, name, function, hook):
        self.modules.add(IASTModule(name, function, hook))

    def add_module_forced(self, name, function, hook):
        self.modules.add(IASTModule(name, function, hook, True))

    def patch(self):
        for module in self.modules:
            if module.patch():
                log.debug("Wrapping %s", module)
                if self.testing:
                    MODULES_TO_UNPATCH.add(module)

    def testing_unpatch(self):
        log.debug("Testing: %s. Unwrapping %s", self.testing, len(MODULES_TO_UNPATCH))
        if self.testing:
            for module in MODULES_TO_UNPATCH.copy():
                log.debug("Unwrapping %s", module)
                module.unpatch()
                MODULES_TO_UNPATCH.remove(module)


def _testing_unpatch_iast():
    warp_modules = WrapModulesForIAST()
    warp_modules.testing_unpatch()
