import sys

from .hook import (
    register_post_import_hook,
    deregister_post_import_hook,
)


def install_module_import_hook(modulename, modulehook):
    """Installs a module import hook for a given module.

    A flag is also stored and checked on the module to so that the module is
    not instrumented more than once.
    """
    # wrap the module hook with an idempotence check
    def check_patched_hook(module):
        if module_patched(module):
            return
        _mark_module_patched(module)
        modulehook(module)
    setattr(check_patched_hook, '_datadog_hook', True)
    register_post_import_hook(check_patched_hook, modulename)


def _hook_matcher(hook):
    """Matches functions that are hooks."""
    return hasattr(hook, '_datadog_hook')


def uninstall_module_import_hook(modulename):
    """Uninstalls a module import hook for a module given its name.

    Note: any patching done on the module will still apply, this function
    simply removes/disables the import hook.
    """
    if modulename in sys.modules:
        module = sys.modules[modulename]
        _mark_module_unpatched(module)
    deregister_post_import_hook(modulename, _hook_matcher)


def _mark_module_patched(module):
    """
    Marks a module as being patched.
    """
    setattr(module, '_datadog_patch', True)


def module_patched(module):
    """
    Returns whether a given module is patched.
    """
    return getattr(module, '_datadog_patch', False)


def _mark_module_unpatched(module):
    """
    Marks a module as being unpatched.
    """
    if not getattr(module, '_datadog_patch', False):
        return

    setattr(module, '_datadog_patch', False)
