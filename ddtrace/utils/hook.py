"""
This module is based off of wrapt.importer (wrapt==1.11.0)
https://github.com/GrahamDumpleton/wrapt/blob/4bcd190457c89e993ffcfec6dad9e9969c033e9e/src/wrapt/importer.py#L127-L136

The reasoning for this is that wrapt.importer does not provide a mechanism to
remove the import hooks and that wrapt removes the hooks after they are fired.

So this module differs from wrapt.importer in that:
    - deregister_post_import_hook is introduced to remove hooks
    - notify_module_loaded is modified to not remove the hooks when they are
      fired.
"""
import logging
import sys
import threading

from wrapt.decorators import synchronized

from ddtrace.compat import PY3, string_type


log = logging.getLogger(__name__)

string_types = string_type,


# The dictionary registering any post import hooks to be triggered once
# the target module has been imported. Once a module has been imported
# and the hooks fired, the list of hooks recorded against the target
# module will be truncacted but the list left in the dictionary. This
# acts as a flag to indicate that the module had already been imported.

_post_import_hooks = {}
_post_import_hooks_init = False
_post_import_hooks_lock = threading.RLock()


def _create_import_hook_from_string(name):
    """
    Register a new post import hook for the target module name. This
    differs from the PEP-369 implementation in that it also allows the
    hook function to be specified as a string consisting of the name of
    the callback in the form 'module:function'. This will result in a
    proxy callback being registered which will defer loading of the
    specified module containing the callback function until required.
    """
    def import_hook(module):
        module_name, function = name.split(':')
        attrs = function.split('.')
        __import__(module_name)
        callback = sys.modules[module_name]
        for attr in attrs:
            callback = getattr(callback, attr)
        return callback(module)
    return import_hook


@synchronized(_post_import_hooks_lock)
def register_post_import_hook(hook, name):
    # Create a deferred import hook if hook is a string name rather than
    # a callable function.

    if isinstance(hook, string_types):
        hook = _create_import_hook_from_string(hook)

    # Automatically install the import hook finder if it has not already
    # been installed.

    global _post_import_hooks_init

    if not _post_import_hooks_init:
        _post_import_hooks_init = True
        sys.meta_path.insert(0, ImportHookFinder())

    # Determine if any prior registration of a post import hook for
    # the target modules has occurred and act appropriately.

    hooks = _post_import_hooks.get(name, None)

    if hooks is None:
        # No prior registration of post import hooks for the target
        # module. We need to check whether the module has already been
        # imported. If it has we fire the hook immediately and add an
        # empty list to the registry to indicate that the module has
        # already been imported and hooks have fired. Otherwise add
        # the post import hook to the registry.

        module = sys.modules.get(name, None)

        if module is not None:
            _post_import_hooks[name] = []
            hook(module)

        else:
            _post_import_hooks[name] = [hook]

    elif hooks == []:
        # A prior registration of port import hooks for the target
        # module was done and the hooks already fired. Fire the hook
        # immediately.

        module = sys.modules[name]
        hook(module)

    else:
        # A prior registration of port import hooks for the target
        # module was done but the module has not yet been imported.

        _post_import_hooks[name].append(hook)


def _create_import_hook_from_entrypoint(entrypoint):
    """
    Register post import hooks defined as package entry points.
    """
    def import_hook(module):
        __import__(entrypoint.module_name)
        callback = sys.modules[entrypoint.module_name]
        for attr in entrypoint.attrs:
            callback = getattr(callback, attr)
        return callback(module)
    return import_hook


@synchronized(_post_import_hooks_lock)
def notify_module_loaded(module):
    """
    Indicate that a module has been loaded. Any post import hooks which
    were registered against the target module will be invoked.

    Any raised exceptions will be caught and an error message indicating
    a module import hook failure will be displayed.
    """
    name = getattr(module, '__name__', None)
    hooks = _post_import_hooks.get(name, None)

    if not hooks:
        return

    for hook in hooks:
        try:
            hook(module)
        except Exception as err:
            log.warn('failed to call hook for module "{}": {}'.format(name, err))


class _ImportHookLoader(object):
    """
    A custom module import finder. This intercepts attempts to import
    modules and watches out for attempts to import target modules of
    interest. When a module of interest is imported, then any post import
    hooks which are registered will be invoked.
    """
    def load_module(self, fullname):
        module = sys.modules[fullname]
        notify_module_loaded(module)

        return module


class _ImportHookChainedLoader(object):
    def __init__(self, loader):
        self.loader = loader

    def load_module(self, fullname):
        module = self.loader.load_module(fullname)
        notify_module_loaded(module)

        return module


class ImportHookFinder:
    def __init__(self):
        self.in_progress = {}

    @synchronized(_post_import_hooks_lock)
    def find_module(self, fullname, path=None):
        # If the module being imported is not one we have registered
        # post import hooks for, we can return immediately. We will
        # take no further part in the importing of this module.

        if fullname not in _post_import_hooks:
            return None

        # When we are interested in a specific module, we will call back
        # into the import system a second time to defer to the import
        # finder that is supposed to handle the importing of the module.
        # We set an in progress flag for the target module so that on
        # the second time through we don't trigger another call back
        # into the import system and cause a infinite loop.

        if fullname in self.in_progress:
            return None

        self.in_progress[fullname] = True

        # Now call back into the import system again.

        try:
            if PY3:
                # For Python 3 we need to use find_spec().loader
                # from the importlib.util module. It doesn't actually
                # import the target module and only finds the
                # loader. If a loader is found, we need to return
                # our own loader which will then in turn call the
                # real loader to import the module and invoke the
                # post import hooks.
                try:
                    import importlib.util
                    loader = importlib.util.find_spec(fullname).loader
                except (ImportError, AttributeError):
                    loader = importlib.find_loader(fullname, path)
                if loader:
                    return _ImportHookChainedLoader(loader)

            else:
                # For Python 2 we don't have much choice but to
                # call back in to __import__(). This will
                # actually cause the module to be imported. If no
                # module could be found then ImportError will be
                # raised. Otherwise we return a loader which
                # returns the already loaded module and invokes
                # the post import hooks.

                __import__(fullname)

                return _ImportHookLoader()

        finally:
            del self.in_progress[fullname]


@synchronized(_post_import_hooks_lock)
def deregister_post_import_hook(modulename, matcher):
    """
    Deregisters post import hooks for a module given the module name and a
    matcher function. All hooks matching the matcher function will be removed.
    """
    hooks = _post_import_hooks.get(modulename, []) or []
    hooks = list(filter(lambda h: not matcher(h), hooks))

    # Work around for wrapt since wrapt assumes that if
    # _post_import_hooks.get(modulename) is not None then the module must have
    # been imported.
    if not len(hooks):
        hooks = None
    _post_import_hooks[modulename] = hooks
