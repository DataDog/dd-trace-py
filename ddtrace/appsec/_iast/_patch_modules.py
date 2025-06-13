from typing import Set

from ddtrace.appsec._common_module_patches import try_unwrap
from ddtrace.appsec._common_module_patches import try_wrap_function_wrapper


MODULES_TO_UNPATCH: Set["IASTModule"] = set()


class IASTModule:
    name = ""
    function = ""
    hook = ""

    def __init__(self, name, function, hook):
        self.name = name
        self.function = function
        self.hook = hook

    def patch(self):
        try_wrap_function_wrapper(self.name, self.function, self.hook)
        return True

    def unpatch(self):
        try_unwrap(self.name, self.function)


class WrapModulesForIAST:
    modules: Set[IASTModule] = set()
    testing: bool = False

    def __init__(self, testing=False):
        self.testing = testing

    def add_module(self, name, function, hook):
        self.modules.add(IASTModule(name, function, hook))

    def patch(self):
        for module in self.modules:
            if module.patch() and self.testing:
                MODULES_TO_UNPATCH.add(module)

    def testing_unpatch(self):
        if self.testing:
            for module in MODULES_TO_UNPATCH:
                module.unpatch()
                MODULES_TO_UNPATCH.remove(module)
