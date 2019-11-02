from collections import defaultdict
import sys

from ..logger import get_logger

log = get_logger(__name__)


class ModuleHookRegistry(object):
    """
    Registry to keep track of all module import hooks defined
    """
    __slots__ = ('hooks', )

    def __init__(self):
        """
        Initialize a new registry
        """
        self.reset()

    def register(self, name, func):
        """
        Register a new hook function for the provided module name

        If the module is already loaded, then ``func`` is called immediately

        :param name: The name of the module to add the hook for (e.g. 'requests', 'flask.app', etc)
        :type name: str
        :param func: The function to register as a hook
        :type func: function(module)
        """
        self.hooks[name].add(func)

        # Module is already loaded, call hook right away
        if name in sys.modules:
            func(sys.modules[name])

    def deregister(self, name, func):
        """
        Deregister an already registered hook function

        :param name: The name of the module the hook was for (e.g. 'requests', 'flask.app', etc)
        :type name: str
        :param func: The function that was registered previously
        :type func: function(module)
        """
        # If no hooks exist for this module, return
        if name not in self.hooks:
            return

        # Remove this function from the hooks if exists
        if func in self.hooks[name]:
            self.hooks[name].remove(func)

    def call(self, name, module=None):
        """
        Call all hooks for the provided module

        If no module was provided then we will attempt to grab from ``sys.modules`` first

        :param name: The name of the module to call hooks for (e.g. 'requests', 'flask.app', etc)
        :type name: str
        :param module: Optional, the module object to pass to hook functions
        :type module: Module|None
        """
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
            try:
                hook(module)
            except Exception:
                log.debug('Failed to call hook %r for module %r', hook, name, exec_info=True)

    def reset(self):
        """Reset/remove all registered hooks"""
        self.hooks = defaultdict(set)


# Default/global module hook registry
hooks = ModuleHookRegistry()
