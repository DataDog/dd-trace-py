"""
the autopatch module will attempt to automatically monkeypatch
all available contrib modules.

It is currently experimental and incomplete.
"""


import logging
import importlib
import threading


log = logging.getLogger()


# modules which are monkeypatch'able
autopatch_modules = [
    'requests',
    'sqlite3',
    'psycopg',
    'redis',
]

_lock = threading.Lock()
_patched_modules = set()

def get_patched_modules():
    with _lock:
        return sorted(_patched_modules)

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
    with _lock:
        if path in _patched_modules:
            log.debug("already patched: %s", path)
            return False

        log.debug("attempting to patch %s", path)
        imp = importlib.import_module(path)

        func = getattr(imp, 'patch', None)
        if func is None:
            log.debug('no patch function in %s. skipping', path)
            return False

        func()
        log.debug("patched")
        _patched_modules.add(path)
        return True
