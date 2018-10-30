from wrapt.importer import register_post_import_hook


def install_module_import_hook(modulename, modulehook):
    """Installs a module import hook for a given module.

    A flag is also stored and checked on the module to so that the module is
    not instrumented more than once.
    """
    # wrap the module hook with an idempotence check
    def checked_hook(module):
        if getattr(module, '_datadog_patch', False):
            return
        setattr(module, '_datadog_patch', True)
        modulehook(module)

    register_post_import_hook(checked_hook, modulename)


def uninstall_module_import_hooks(modulename):
    """
    TODO
    """
    raise NotImplementedError()
