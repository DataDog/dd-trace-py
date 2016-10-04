"""
the autopatch module will attempt to automatically monkeypatch
all available contrib modules.

It is currently experimental and incomplete.
"""


import logging
import importlib


log = logging.getLogger()


# modules which are monkeypatch'able
autopatch_modules = [
    'requests',
]


def autopatch():
    """ autopatch will attempt to patch all available contrib modules. """
    for module in autopatch_modules:
        path = 'ddtrace.contrib.%s' % module
        patch_module(path)

def patch_module(path):
    """ patch_module will attempt to autopatch the module with the given
        import path.
    """
    log.debug("attempting to patch %s", path)
    imp = importlib.import_module(path)

    func = getattr(imp, 'patch', None)
    if func is None:
        log.debug('no patch function in %s. skipping', path)
        return False

    log.debug("calling patch func %s in %s", func, path)
    func()
    log.debug("patched")
    return True

if __name__ == '__main__':
    autopatch()
