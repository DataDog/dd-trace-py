import sys

from wrapt.importer import (
    register_post_import_hook,
    _post_import_hooks,
    _post_import_hooks_lock,
)
from wrapt.decorators import synchronized


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
    simply removed/disables the import hook.
    """
    if modulename in sys.modules:
        module = __import__(modulename)
        _mark_module_unpatched(module)
    _deregister_post_import_hook(modulename, _hook_matcher)


@synchronized(_post_import_hooks_lock)
def _deregister_post_import_hook(modulename, matcher):
    """
    Deregisters post import hooks for a module given the module name and a
    matcher function. All hooks matching the matcher function will be removed.
    """
    hooks = _post_import_hooks.get(modulename, []) or []
    hooks = list(filter(lambda h: not matcher(h), hooks))

    # Work around for wrapt since wrapt assumes that if
    # _post_import_hooks.get(modulename) is a list then the module must have
    # been imported.
    if not len(hooks):
        hooks = None
    _post_import_hooks[modulename] = hooks


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
