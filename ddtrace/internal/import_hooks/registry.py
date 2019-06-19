from collections import defaultdict
import sys


class ModuleHookRegistry:
    __slots__ = ('hooks', )

    def __init__(self):
        self.hooks = defaultdict(set)

    def register(self, name, func):
        self.hooks[name].add(func)

        # Module is already loaded, call hook right away
        if name in sys.modules:
            func(sys.modules[name])

    def deregister(self, name, func):
        if name not in self.hooks:
            return

        if func in self.hooks[name]:
            self.hooks[name].remove(func)

    def call(self, name, module=None):
        # If no name was provided, exit early
        if not name:
            return

        # Make sure we have hooks for this module
        if name not in self.hooks:
            return

        # Try to fetch from `sys.modules` if one wasn't given directly
        if module is None:
            module = sys.modules.get(name)

        # No module found, don't call anything
        if not module:
            return

        # Call all hooks for this module
        for hook in self.hooks[name]:
            hook(module)


hooks = ModuleHookRegistry()
