import importlib
import os
import sys
import threading
from typing import Any
from typing import Callable
from typing import List

from ddtrace.vendor.wrapt.importer import when_imported

from .internal.logger import get_logger
from .internal.utils import formats
from .internal.utils.deprecation import deprecated
from .settings import _config as config


log = get_logger(__name__)

# Default set of modules to automatically patch or not
PATCH_MODULES = {
    "aioredis": True,
    "aredis": True,
    "asyncio": True,
    "boto": True,
    "botocore": True,
    "bottle": False,
    "cassandra": True,
    "celery": True,
    "consul": True,
    "django": True,
    "elasticsearch": True,
    "algoliasearch": True,
    "futures": True,
    "grpc": True,
    "httpx": True,
    "mongoengine": True,
    "mysql": True,
    "mysqldb": True,
    "pymysql": True,
    "mariadb": True,
    "psycopg": True,
    "pylibmc": True,
    "pymemcache": True,
    "pymongo": True,
    "redis": True,
    "rediscluster": True,
    "requests": True,
    "rq": True,
    "sanic": True,
    "snowflake": False,
    "sqlalchemy": False,  # Prefer DB client instrumentation
    "sqlite3": True,
    "aiohttp": True,  # requires asyncio (Python 3.4+)
    "aiopg": True,
    "aiobotocore": False,
    "httplib": False,
    "urllib3": False,
    "vertica": True,
    "molten": True,
    "jinja2": True,
    "mako": True,
    "flask": True,
    "kombu": False,
    "starlette": True,
    # Ignore some web framework integrations that might be configured explicitly in code
    "falcon": False,
    "pylons": False,
    "pyramid": False,
    # Auto-enable logging if the environment variable DD_LOGS_INJECTION is true
    "logging": config.logs_injection,
    "pynamodb": True,
    "pyodbc": True,
    "fastapi": True,
    "dogpile_cache": True,
    "yaaredis": True,
}

_LOCK = threading.Lock()
_PATCHED_MODULES = set()

# Modules which are patched on first use
# DEV: These modules are patched when the user first imports them, rather than
#      explicitly importing and patching them on application startup `ddtrace.patch_all(module=True)`
# DEV: This ensures we do not patch a module until it is needed
# DEV: <contrib name> => <list of module names that trigger a patch>
_PATCH_ON_IMPORT = {
    "aiohttp": ("aiohttp",),
    "aiobotocore": ("aiobotocore",),
    "celery": ("celery",),
    "flask": ("flask",),
    "gevent": ("gevent",),
    "requests": ("requests",),
    "botocore": ("botocore",),
    "elasticsearch": (
        "elasticsearch",
        "elasticsearch2",
        "elasticsearch5",
        "elasticsearch6",
        "elasticsearch7",
    ),
    "pynamodb": ("pynamodb",),
}


class PatchException(Exception):
    """Wraps regular `Exception` class when patching modules"""

    pass


class ModuleNotFoundException(PatchException):
    pass


def _on_import_factory(module, raise_errors=True):
    # type: (str, bool) -> Callable[[Any], None]
    """Factory to create an import hook for the provided module name"""

    def on_import(hook):
        # Import and patch module
        path = "ddtrace.contrib.%s" % module
        try:
            imported_module = importlib.import_module(path)
        except ImportError:
            if raise_errors:
                raise
            log.error("failed to import ddtrace module %r when patching on import", path, exc_info=True)
        else:
            imported_module.patch()

    return on_import


def patch_all(**patch_modules):
    # type: (bool) -> None
    """Automatically patches all available modules.

    In addition to ``patch_modules``, an override can be specified via an
    environment variable, ``DD_TRACE_<module>_ENABLED`` for each module.

    ``patch_modules`` have the highest precedence for overriding.

    :param dict patch_modules: Override whether particular modules are patched or not.

        >>> patch_all(redis=False, cassandra=False)
    """
    modules = PATCH_MODULES.copy()

    # The enabled setting can be overridden by environment variables
    for module, enabled in modules.items():
        env_var = "DD_TRACE_%s_ENABLED" % module.upper()
        if env_var not in os.environ:
            continue

        override_enabled = formats.asbool(os.environ[env_var])
        modules[module] = override_enabled

    # Arguments take precedence over the environment and the defaults.
    modules.update(patch_modules)

    patch(raise_errors=False, **modules)


def patch(raise_errors=True, **patch_modules):
    # type: (bool, bool) -> None
    """Patch only a set of given modules.

    :param bool raise_errors: Raise error if one patch fail.
    :param dict patch_modules: List of modules to patch.

        >>> patch(psycopg=True, elasticsearch=True)
    """
    modules = [m for (m, should_patch) in patch_modules.items() if should_patch]
    for module in modules:
        if module in _PATCH_ON_IMPORT:
            modules_to_poi = _PATCH_ON_IMPORT[module]
            for m in modules_to_poi:
                # If the module has already been imported then patch immediately
                if m in sys.modules:
                    _patch_module(module, raise_errors=raise_errors)
                    break
                # Otherwise, add a hook to patch when it is imported for the first time
                else:
                    # Use factory to create handler to close over `module` and `raise_errors` values from this loop
                    when_imported(m)(_on_import_factory(module, raise_errors))

            # manually add module to patched modules
            with _LOCK:
                _PATCHED_MODULES.add(module)
        else:
            _patch_module(module, raise_errors=raise_errors)

    patched_modules = _get_patched_modules()
    log.info(
        "patched %s/%s modules (%s)",
        len(patched_modules),
        len(modules),
        ",".join(patched_modules),
    )


@deprecated(
    message="This function will be removed.",
    version="1.0.0",
)
def patch_module(module, raise_errors=True):
    # type: (str, bool) -> bool
    return _patch_module(module, raise_errors=raise_errors)


def _patch_module(module, raise_errors=True):
    # type: (str, bool) -> bool
    """Patch a single module

    Returns if the module got properly patched.
    """
    try:
        return _attempt_patch_module(module)
    except ModuleNotFoundException:
        if raise_errors:
            raise
        return False
    except Exception:
        if raise_errors:
            raise
        log.debug("failed to patch %s", module, exc_info=True)
        return False


@deprecated(
    message="This function will be removed.",
    version="1.0.0",
)
def get_patched_modules():
    # type: () -> List[str]
    return _get_patched_modules()


def _get_patched_modules():
    # type: () -> List[str]
    """Get the list of patched modules"""
    with _LOCK:
        return sorted(_PATCHED_MODULES)


def _attempt_patch_module(module):
    # type: (str) -> bool
    """_patch_module will attempt to monkey patch the module.

    Returns if the module got patched.
    Can also raise errors if it fails.
    """
    path = "ddtrace.contrib.%s" % module
    with _LOCK:
        if module in _PATCHED_MODULES and module not in _PATCH_ON_IMPORT:
            log.debug("already patched: %s", path)
            return False

        try:
            imported_module = importlib.import_module(path)
        except ImportError:
            # if the import fails, the integration is not available
            raise ModuleNotFoundException(
                "integration module %s does not exist, module will not have tracing available" % path
            )
        else:
            # if patch() is not available in the module, it means
            # that the library is not installed in the environment
            if not hasattr(imported_module, "patch"):
                raise AttributeError(
                    "%s.patch is not found. '%s' is not configured for this environment" % (path, module)
                )

            imported_module.patch()  # type: ignore
            _PATCHED_MODULES.add(module)
            return True
