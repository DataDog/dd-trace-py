import importlib
import os
from types import ModuleType
from typing import TYPE_CHECKING  # noqa:F401
from typing import Set
from typing import Union

from wrapt.importer import when_imported

from ddtrace.appsec._listeners import load_common_appsec_modules
from ddtrace.internal.compat import Path
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.settings._config import config
from ddtrace.vendor.debtcollector import deprecate
from ddtrace.vendor.packaging.specifiers import SpecifierSet
from ddtrace.vendor.packaging.version import Version

from .internal import telemetry
from .internal.logger import get_logger
from .internal.utils import formats
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any  # noqa:F401
    from typing import Callable  # noqa:F401
    from typing import List  # noqa:F401


log = get_logger(__name__)

# Default set of modules to automatically patch or not
PATCH_MODULES = {
    "aioredis": True,
    "aiomysql": True,
    "aredis": True,
    "asyncio": True,
    "avro": True,
    "boto": True,
    "botocore": True,
    "bottle": True,
    "cassandra": True,
    "celery": True,
    "consul": True,
    "ddtrace_api": True,
    "django": True,
    "dramatiq": True,
    "elasticsearch": True,
    "algoliasearch": True,
    "futures": True,
    "freezegun": False,  # deprecated, to be removed in ddtrace 4.x
    "google_generativeai": True,
    "google_genai": True,
    "gevent": True,
    "graphql": True,
    "grpc": True,
    "httpx": True,
    "kafka": True,
    "langgraph": True,
    "litellm": True,
    "mongoengine": True,
    "mysql": True,
    "mysqldb": True,
    "pymysql": True,
    "mariadb": True,
    "mcp": True,
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
    "vertexai": True,
    "vertica": True,
    "molten": True,
    "jinja2": True,
    "mako": True,
    "flask": True,
    "kombu": False,
    "starlette": True,
    # Ignore some web framework integrations that might be configured explicitly in code
    "falcon": True,
    "pyramid": True,
    "logbook": True,
    "logging": True,
    "loguru": True,
    "structlog": True,
    "pynamodb": True,
    "pyodbc": True,
    "fastapi": True,
    "dogpile_cache": True,
    "yaaredis": True,
    "asyncpg": True,
    "aws_lambda": True,  # patch only in AWS Lambda environments
    "azure_functions": True,
    "azure_servicebus": True,
    "tornado": False,
    "openai": True,
    "langchain": True,
    "anthropic": True,
    "crewai": True,
    "pydantic_ai": True,
    "subprocess": True,
    "unittest": True,
    "coverage": False,
    "selenium": True,
    "valkey": True,
    "openai_agents": True,
    "protobuf": config._data_streams_enabled,
}


# this information would make sense to live in the contrib modules,
# but that would mean getting it would require importing those modules,
# which we need to avoid until as late as possible.
CONTRIB_DEPENDENCIES = {
    "tornado": ("futures",),
}


_PATCHED_MODULES = set()

# Module names that need to be patched for a given integration. If the module
# name coincides with the integration name, then there is no need to add an
# entry here.
_MODULES_FOR_CONTRIB = {
    "elasticsearch": (
        "elasticsearch",
        "elasticsearch1",
        "elasticsearch2",
        "elasticsearch5",
        "elasticsearch6",
        "elasticsearch7",
        # Starting with version 8, the default transport which is what we
        # actually patch is found in the separate elastic_transport package
        "elastic_transport",
        "opensearchpy",
    ),
    "psycopg": (
        "psycopg",
        "psycopg2",
    ),
    "snowflake": ("snowflake.connector",),
    "cassandra": ("cassandra.cluster",),
    "dogpile_cache": ("dogpile.cache",),
    "mysqldb": ("MySQLdb",),
    "futures": ("concurrent.futures.thread",),
    "vertica": ("vertica_python",),
    "aws_lambda": ("datadog_lambda",),
    "azure_functions": ("azure.functions",),
    "azure_servicebus": ("azure.servicebus",),
    "httplib": ("http.client",),
    "kafka": ("confluent_kafka",),
    "google_generativeai": ("google.generativeai",),
    "google_genai": ("google.genai",),
    "langgraph": (
        "langgraph",
        "langgraph.graph",
        "langgraph.prebuilt",
    ),
    "openai_agents": ("agents",),
}

_NOT_PATCHABLE_VIA_ENVVAR = {"ddtrace_api"}


class PatchException(Exception):
    """Wraps regular `Exception` class when patching modules"""

    pass


class ModuleNotFoundException(PatchException):
    pass


class IncompatibleModuleException(PatchException):
    def __init__(self, message: str, installed_version: Union[str, None] = None):
        super().__init__(message)
        self.installed_version = installed_version


def is_version_compatible(version: str, supported_versions_spec: str) -> bool:
    "Returns whether a given package version is compatible with the integration's supported version range."

    if not supported_versions_spec:
        return False

    if supported_versions_spec == "*":
        return True

    try:
        specifier_set = SpecifierSet(supported_versions_spec)
        return Version(version) in specifier_set
    except Exception:
        return False


def _get_installed_module_version(imported_module: ModuleType, hooked_module_name: str) -> Union[str, None]:
    "Returns the installed version of a module."

    if hasattr(imported_module, "get_versions"):
        return imported_module.get_versions().get(hooked_module_name)
    elif hasattr(imported_module, "get_version"):
        return imported_module.get_version()
    return None


def _get_integration_supported_versions(
    integration_patch_module: ModuleType, integration_name: str, hooked_module_name: str
) -> Union[str, None]:
    "Returns the supported version range for an integration."
    if not hasattr(integration_patch_module, "_supported_versions"):
        return None

    supported_versions = integration_patch_module._supported_versions()
    if hooked_module_name in supported_versions:
        return supported_versions[hooked_module_name]
    elif integration_name in supported_versions:
        return supported_versions[integration_name]
    return None


def check_module_compatibility(
    integration_patch_module: ModuleType, integration_name: str, hooked_module_name: str
) -> None:
    "Determines if a module should be patched based on installed version and the integration's supported version range."

    # stdlib modules will not have an associated version and should always be patched
    installed_version = _get_installed_module_version(integration_patch_module, hooked_module_name)
    if not installed_version:
        return

    supported_version_spec = _get_integration_supported_versions(
        integration_patch_module, integration_name, hooked_module_name
    )
    if not supported_version_spec:
        # TODO: once all integrations have a supported version spec, we should raise an error here
        return

    if not is_version_compatible(installed_version, supported_version_spec):
        message = (
            f"Skipped patching '{integration_name}' integration, installed version: {installed_version} "
            f"is not compatible with integration support spec: {supported_version_spec}."
        )
        raise IncompatibleModuleException(message, installed_version=installed_version)
    return


def _on_import_factory(module, path_f, raise_errors=True, patch_indicator=True):
    # type: (str, str, bool, Union[bool, List[str]]) -> Callable[[Any], None]
    """Factory to create an import hook for the provided module name"""

    def on_import(hook):
        # Import and patch module
        try:
            imported_module = importlib.import_module(path_f % (module,))

            # if safe instrumentation is enabled, we check if the module's version
            # is compatible with the integration's supported version range, and throw an error if it is not
            if config._trace_safe_instrumentation_enabled:
                check_module_compatibility(imported_module, module, hook.__name__)

            imported_module.patch()
            if hasattr(imported_module, "patch_submodules"):
                imported_module.patch_submodules(patch_indicator)

        except IncompatibleModuleException as e:
            log.error(
                "failed to enable ddtrace support for %s: %s",
                module,
                str(e),
            )
            telemetry.telemetry_writer.add_integration(
                module, False, PATCH_MODULES.get(module) is True, str(e), version=e.installed_version
            )
        except Exception as e:
            if raise_errors:
                raise
            log.error(
                "failed to enable ddtrace support for %s: %s",
                module,
                str(e),
            )
            telemetry.telemetry_writer.add_integration(module, False, PATCH_MODULES.get(module) is True, str(e))
            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.TRACERS,
                "integration_errors",
                1,
                (("integration_name", module), ("error_type", type(e).__name__)),
            )
        else:
            if hasattr(imported_module, "get_versions"):
                versions = imported_module.get_versions()
                for name, v in versions.items():
                    telemetry.telemetry_writer.add_integration(
                        name, True, PATCH_MODULES.get(module) is True, "", version=v
                    )
            elif hasattr(imported_module, "get_version"):
                # Some integrations/iast patchers do not define get_version
                version = imported_module.get_version()
                telemetry.telemetry_writer.add_integration(
                    module, True, PATCH_MODULES.get(module) is True, "", version=version
                )

    return on_import


def patch_all(**patch_modules: bool) -> None:
    """Enables ddtrace library instrumentation.

    In addition to ``patch_modules``, an override can be specified via an
    environment variable, ``DD_TRACE_<module>_ENABLED`` for each module.

    ``patch_modules`` have the highest precedence for overriding.

    :param dict patch_modules: Override whether particular modules are patched or not.

        >>> _patch_all(redis=False, cassandra=False)
    """
    deprecate(
        "patch_all is deprecated and will be removed in a future version of the tracer.",
        message="""patch_all is deprecated in favor of ``import ddtrace.auto`` and ``DD_PATCH_MODULES``
        environment variable if needed.""",
        category=DDTraceDeprecationWarning,
    )
    _patch_all(**patch_modules)


def _patch_all(**patch_modules: bool) -> None:
    modules = PATCH_MODULES.copy()

    # The enabled setting can be overridden by environment variables
    for module, _enabled in modules.items():
        env_var = "DD_TRACE_%s_ENABLED" % module.upper()
        if module not in _NOT_PATCHABLE_VIA_ENVVAR and env_var in os.environ:
            modules[module] = formats.asbool(os.environ[env_var])

        # Enable all dependencies for the module
        if modules[module]:
            for dep in CONTRIB_DEPENDENCIES.get(module, ()):
                modules[dep] = True

    # Arguments take precedence over the environment and the defaults.
    modules.update(patch_modules)

    patch(raise_errors=False, **modules)

    load_common_appsec_modules()


def patch(raise_errors=True, **patch_modules):
    # type: (bool, Union[List[str], bool]) -> None
    """Patch only a set of given modules.

    :param bool raise_errors: Raise error if one patch fail.
    :param dict patch_modules: List of modules to patch.

        >>> patch(psycopg=True, elasticsearch=True)
    """
    contribs = {c: patch_indicator for c, patch_indicator in patch_modules.items() if patch_indicator}
    for contrib, patch_indicator in contribs.items():
        # Check if we have the requested contrib.
        if not (Path(__file__).parent / "contrib" / "internal" / contrib / "patch.py").exists():
            if raise_errors:
                raise ModuleNotFoundException(f"{contrib} does not have automatic instrumentation")
        modules_to_patch = _MODULES_FOR_CONTRIB.get(contrib, (contrib,))
        for module in modules_to_patch:
            # Use factory to create handler to close over `module` and `raise_errors` values from this loop
            when_imported(module)(
                _on_import_factory(
                    contrib,
                    "ddtrace.contrib.internal.%s.patch",
                    raise_errors=raise_errors,
                    patch_indicator=patch_indicator,
                )
            )

        # manually add module to patched modules
        _PATCHED_MODULES.add(contrib)

    log.info(
        "Configured ddtrace instrumentation for %s integration(s). The following modules have been patched: %s",
        len(contribs),
        ",".join(contribs),
    )


def _get_patched_modules() -> Set[str]:
    """Get the list of patched modules"""
    return _PATCHED_MODULES
