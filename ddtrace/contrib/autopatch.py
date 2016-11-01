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
    'sqlite3',
    'psycopg',
    'redis',
]


def autopatch():
    """ autopatch will attempt to patch all available contrib modules. """
    patch_modules(autopatch_modules, raise_errors=False)

def patch_modules(modules, raise_errors=False):
    count = 0
    for module in modules:
        path = 'ddtrace.contrib.%s.patch' % module
        patched = False
        try:
            patched = patch_module(path)
        except Exception:
            if raise_errors:
                raise
            else:
                log.debug("couldn't patch %s" % module, exc_info=True)
        if patched:
            count += 1
    log.debug("patched %s/%s modules", count, len(modules))

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

    func()
    log.debug("patched")
    return True
