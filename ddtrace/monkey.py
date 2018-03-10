"""Patch librairies to be automatically instrumented.

It can monkey patch supported standard libraries and third party modules.
A patched module will automatically report spans with its default configuration.

A library instrumentation can be configured (for instance, to report as another service)
using Pin. For that, check its documentation.
"""
import logging
import importlib
import threading


log = logging.getLogger(__name__)

# Default set of modules to automatically patch or not
PATCH_MODULES = {
    'asyncio': False,
    'boto': False,
    'botocore': False,
    'bottle': False,
    'cassandra': True,
    'celery': True,
    'elasticsearch': True,
    'mongoengine': True,
    'mysql': True,
    'mysqldb': True,
    'psycopg': True,
    'pylibmc': True,
    'pymongo': True,
    'redis': True,
    'requests': False,  # Not ready yet
    'sqlalchemy': False,  # Prefer DB client instrumentation
    'sqlite3': True,
    'aiohttp': True,  # requires asyncio (Python 3.4+)
    'aiopg': True,
    'aiobotocore': False,
    'httplib': False,

    # Ignore some web framework integrations that might be configured explicitly in code
    "django": False,
    "flask": False,
    "falcon": False,
    "pylons": False,
    "pyramid": False,
}

_LOCK = threading.Lock()
_PATCHED_MODULES = set()


class PatchException(Exception):
    """Wraps regular `Exception` class when patching modules"""
    pass


def patch_all(**patch_modules):
    """Automatically patches all available modules.

    :param dict \**patch_modules: Override whether particular modules are patched or not.

        >>> patch_all(redis=False, cassandra=False)
    """
    modules = PATCH_MODULES.copy()
    modules.update(patch_modules)

    patch(raise_errors=False, **modules)

def patch(raise_errors=True, **patch_modules):
    """Patch only a set of given modules.

    :param bool raise_errors: Raise error if one patch fail.
    :param dict \**patch_modules: List of modules to patch.

        >>> patch(psycopg=True, elasticsearch=True)
    """
    modules = [m for (m, should_patch) in patch_modules.items() if should_patch]
    count = 0
    for module in modules:
        patched = patch_module(module, raise_errors=raise_errors)
        if patched:
            count += 1

    log.info("patched %s/%s modules (%s)",
        count,
        len(modules),
        ",".join(get_patched_modules()))


def patch_module(module, raise_errors=True):
    """Patch a single module

    Returns if the module got properly patched.
    """
    try:
        return _patch_module(module)
    except Exception as exc:
        if raise_errors:
            raise
        log.debug("failed to patch %s: %s", module, exc)
        return False

def get_patched_modules():
    """Get the list of patched modules"""
    with _LOCK:
        return sorted(_PATCHED_MODULES)

def _patch_module(module):
    """_patch_module will attempt to monkey patch the module.

    Returns if the module got patched.
    Can also raise errors if it fails.
    """
    path = 'ddtrace.contrib.%s' % module
    with _LOCK:
        if module in _PATCHED_MODULES:
            log.debug("already patched: %s", path)
            return False

        try:
            imported_module = importlib.import_module(path)
            imported_module.patch()
        except ImportError:
            # if the import fails, the integration is not available
            raise PatchException('integration not available')
        except AttributeError:
            # if patch() is not available in the module, it means
            # that the library is not installed in the environment
            raise PatchException('module not installed')

        _PATCHED_MODULES.add(module)
        return True
