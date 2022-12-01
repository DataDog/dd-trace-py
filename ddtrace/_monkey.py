import importlib
import os
import sys
import threading
from typing import TYPE_CHECKING

from ddtrace.vendor.wrapt.importer import when_imported

from .constants import IAST_ENV
from .internal.logger import get_logger
from .internal.telemetry import telemetry_writer
from .internal.utils import formats
from .internal.utils.importlib import require_modules
from .settings import _config as config


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import List


log = get_logger(__name__)

# Default set of modules to automatically patch or not
PATCH_MODULES = {
    "aioredis": True,
    "aiomysql": True,
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
    "gevent": True,
    "graphql": True,
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
    "aiohttp_jinja2": True,
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
    "asyncpg": True,
    "tornado": False,
}


CONTRIB_DEPENDENCIES = {
    "tornado": ("futures",),
}


_LOCK = threading.Lock()
_PATCHED_MODULES = set()

# Module names that need to be patched for a given integration. If the module
# name coincides with the integration name, then there is no need to add an
# entry here.
_MODULES_FOR_CONTRIB = {
    "elasticsearch": (
        "elasticsearch",
        "elasticsearch2",
        "elasticsearch5",
        "elasticsearch6",
        "elasticsearch7",
    ),
    "psycopg": ("psycopg2",),
    "snowflake": ("snowflake.connector",),
    "cassandra": ("cassandra.cluster",),
    "dogpile_cache": ("dogpile.cache",),
    "mysqldb": ("MySQLdb",),
    "futures": ("concurrent.futures",),
    "vertica": ("vertica_python",),
}

IAST_PATCH = {
    "weak_hash": True,
    "weak_cipher": True,
}

DEFAULT_MODULES_PREFIX = "ddtrace.contrib"


class PatchException(Exception):
    """Wraps regular `Exception` class when patching modules"""

    pass


class ModuleNotFoundException(PatchException):
    pass


class IntegrationNotAvailableException(PatchException):
    """Exception for when an integration is not available.

    Raised when the module required for an integration is not available.
    """

    pass


def _on_import_factory(module, prefix="ddtrace.contrib", raise_errors=True):
    # type: (str, str, bool) -> Callable[[Any], None]
    """Factory to create an import hook for the provided module name"""

    def on_import(hook):
        # Import and patch module
        path = "%s.%s" % (prefix, module)
        try:
            imported_module = importlib.import_module(path)
        except ImportError:
            if raise_errors:
                raise
            log.error("failed to import ddtrace module %r when patching on import", path, exc_info=True)
        else:
            imported_module.patch()
            telemetry_writer.add_integration(module, PATCH_MODULES.get(module) is True)

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
        if env_var in os.environ:
            modules[module] = formats.asbool(os.environ[env_var])

        # Enable all dependencies for the module
        if modules[module]:
            for dep in CONTRIB_DEPENDENCIES.get(module, ()):
                modules[dep] = True

    # Arguments take precedence over the environment and the defaults.
    modules.update(patch_modules)

    patch(raise_errors=False, **modules)
    patch_iast(**IAST_PATCH)


def patch_iast(**patch_modules):
    # type: (bool) -> None
    """Load IAST vulnerabilities sink points.

    IAST_PATCH: list of implemented vulnerabilities
    """
    iast_enabled = formats.asbool(os.environ.get(IAST_ENV, "false"))
    if iast_enabled:
        # TODO: Devise the correct patching strategy for IAST
        for module in (m for m, e in patch_modules.items() if e):
            when_imported("hashlib")(
                _on_import_factory(module, prefix="ddtrace.appsec.iast.taint_sinks", raise_errors=False)
            )


def patch(raise_errors=True, patch_modules_prefix=DEFAULT_MODULES_PREFIX, **patch_modules):
    # type: (bool, str, bool) -> None
    """Patch only a set of given modules.

    :param bool raise_errors: Raise error if one patch fail.
    :param dict patch_modules: List of modules to patch.

        >>> patch(psycopg=True, elasticsearch=True)
    """
    contribs = [c for c, should_patch in patch_modules.items() if should_patch]
    for contrib in contribs:
        # Check if we have the requested contrib.
        if not os.path.isfile(os.path.join(os.path.dirname(__file__), "contrib", contrib, "__init__.py")):
            if raise_errors:
                raise ModuleNotFoundException(
                    "integration module ddtrace.contrib.%s does not exist, "
                    "module will not have tracing available" % contrib
                )
        modules_to_patch = _MODULES_FOR_CONTRIB.get(contrib, (contrib,))
        for module in modules_to_patch:
            # If the module has already been imported remove it from sys.modules.
            # It will be patched when reloaded by the tracee.
            if module in sys.modules:
                del sys.modules[module]

                # Also remove any child modules
                module_prefix = module + "."
                for k in list(_ for _ in sys.modules if _.startswith(module_prefix)):
                    del sys.modules[k]

            # Use factory to create handler to close over `module` and `raise_errors` values from this loop
            when_imported(module)(_on_import_factory(contrib, raise_errors=False))

        # manually add module to patched modules
        with _LOCK:
            _PATCHED_MODULES.add(contrib)

    patched_modules = _get_patched_modules()
    log.info(
        "patched %s/%s modules (%s)",
        len(patched_modules),
        len(contribs),
        ",".join(patched_modules),
    )


def _patch_module(module, patch_modules_prefix=DEFAULT_MODULES_PREFIX, raise_errors=True):
    # type: (str, str, bool) -> bool
    """Patch a single module

    Returns if the module got properly patched.
    """
    try:
        return _attempt_patch_module(module, patch_modules_prefix=patch_modules_prefix)
    except ModuleNotFoundException:
        if raise_errors:
            raise
        return False
    except IntegrationNotAvailableException as e:
        if raise_errors:
            raise
        log.debug("integration %s not enabled (%s)", module, str(e))  # noqa: G200
        return False
    except Exception:
        if raise_errors:
            raise
        log.debug("failed to patch %s", module, exc_info=True)
        return False


def _get_patched_modules():
    # type: () -> List[str]
    """Get the list of patched modules"""
    with _LOCK:
        return sorted(_PATCHED_MODULES)


def _attempt_patch_module(module, patch_modules_prefix=DEFAULT_MODULES_PREFIX):
    # type: (str, str) -> bool
    """_patch_module will attempt to monkey patch the module.

    Returns if the module got patched.
    Can also raise errors if it fails.
    """
    path = "%s.%s" % (patch_modules_prefix, module)
    with _LOCK:
        if module in _PATCHED_MODULES and module not in _MODULES_FOR_CONTRIB:
            log.debug("already patched: %s", path)
            return False

        try:
            imported_module = importlib.import_module(path)
        except ImportError:
            # if the import fails, the integration is not available
            raise ModuleNotFoundException(
                "integration module %s does not exist, module will not have tracing available" % path
            )

        # if patch() is not available in the module, it means
        # that the library is not installed in the environment
        if not hasattr(imported_module, "patch"):
            required_mods = getattr(imported_module, "required_modules", [])
            with require_modules(required_mods) as not_avail_mods:
                pass
            raise IntegrationNotAvailableException(
                "missing required module%s: %s" % ("s" if len(not_avail_mods) > 1 else "", ",".join(not_avail_mods))
            )

        imported_module.patch()
        _PATCHED_MODULES.add(module)
        telemetry_writer.add_integration(module, PATCH_MODULES.get(module) is True)
        return True
