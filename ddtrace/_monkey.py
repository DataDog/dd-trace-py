from collections.abc import Callable
import importlib
from pathlib import Path
from typing import Any
from typing import Union

from wrapt.importer import when_imported

from ddtrace.internal import integrations
from ddtrace.internal.integrations import registry as _integration_registry
from ddtrace.internal.settings import env
from ddtrace.internal.settings._config import config
from ddtrace.internal.settings.integration import _integration_env_var_id
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.deprecations import deprecate

from .internal import telemetry
from .internal.logger import get_logger
from .internal.utils import formats
from .internal.utils.deprecations import DDTraceDeprecationWarning  # noqa: E402


log = get_logger(__name__)

# Default set of modules to automatically patch or not
PATCH_MODULES = {
    "aiokafka": True,
    "aiomysql": True,
    "anyio": True,
    "aredis": True,
    "asyncio": True,
    "avro": True,
    "boto": True,
    "botocore": True,
    "bottle": True,
    "celery": True,
    "consul": True,
    "ddtrace_api": True,
    "django": True,
    "dramatiq": True,
    "elasticsearch": True,
    "algoliasearch": True,
    "futures": True,
    "google_adk": True,
    "google_cloud_pubsub": True,
    "google_genai": True,
    "gevent": True,
    "graphql": True,
    "grpc": True,
    "httpx2": True,
    "httpx": True,
    "kafka": True,
    "langgraph": True,
    "llama_index": True,
    "litellm": True,
    "mysql": True,
    "mysqldb": True,
    "pymysql": True,
    "mariadb": True,
    "mcp": True,
    "mistralai": True,
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
    "aws_durable_execution_sdk_python": True,
    "aws_lambda": True,  # patch only in AWS Lambda environments
    "azure_cosmos": True,
    "azure_eventhubs": True,
    "azure_functions": True,
    "azure_durable_functions": True,
    "azure_servicebus": True,
    "tornado": False,
    "trio": True,
    "openai": True,
    "langchain": True,
    "anthropic": True,
    "crewai": True,
    "pydantic_ai": True,
    "pytorch": False,
    "vllm": True,
    "mlflow": config._model_lab_enabled,
    "subprocess": True,
    "unittest": True,
    "coverage": False,
    "selenium": True,
    "valkey": True,
    "openai_agents": True,
    "ray": False,
    "protobuf": config._data_streams_enabled,
    "claude_agent_sdk": True,
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
    "dogpile_cache": ("dogpile.cache",),
    "mysqldb": ("MySQLdb",),
    "futures": ("concurrent.futures.thread",),
    "vertica": ("vertica_python",),
    "aws_lambda": ("datadog_lambda",),
    "azure_cosmos": ("azure.cosmos",),
    "azure_eventhubs": ("azure.eventhub",),
    "azure_durable_functions": ("azure.durable_functions",),
    "azure_functions": ("azure.functions",),
    "azure_servicebus": ("azure.servicebus",),
    "httplib": ("http.client",),
    "kafka": ("confluent_kafka",),
    "google_adk": ("google.adk",),
    "google_cloud_pubsub": ("google.cloud.pubsub_v1",),
    "google_genai": ("google.genai",),
    "langchain": ("langchain_core",),
    "llama_index": ("llama_index.core",),
    "langgraph": (
        "langgraph",
        "langgraph.graph",
        "langgraph.prebuilt",
    ),
    "mistralai": ("mistralai.client",),
    "openai_agents": ("agents",),
    "pytorch": ("torch",),
}

_NOT_PATCHABLE_VIA_ENVVAR = {"ddtrace_api"}

# Import from the integratuin plugin interface while integrations are still
# migrated.
IntegrationException = integrations.IntegrationException
ModuleNotFoundException = integrations.ModuleNotFoundException
IncompatibleModuleException = integrations.IncompatibleModuleException
is_version_compatible = integrations.is_version_compatible
check_module_compatibility = integrations.check_module_compatibility


def _on_import_factory(
    module: str, path_f: str, raise_errors: bool = True, patch_indicator: Union[bool, list[str]] = True
) -> Callable[[Any], None]:
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
                extra={"send_to_telemetry": False},
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
                exc_info=True,
                extra={"send_to_telemetry": False},
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

        >>> _patch_all(redis=False)
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

    # Merge in migrated plugins discovered via the "ddtrace.integrations"
    # entry-point group (IntegrationRegistry) that aren't already covered by
    # PATCH_MODULES.
    for plugin in _integration_registry:
        modules.setdefault(plugin.name, plugin.default_enabled)

    # The enabled setting can be overridden by environment variables
    for module, _enabled in modules.items():
        env_var = "DD_TRACE_%s_ENABLED" % _integration_env_var_id(module)
        if module not in _NOT_PATCHABLE_VIA_ENVVAR and env_var in env:
            modules[module] = formats.asbool(env[env_var])

        # Enable all dependencies for the module
        if modules[module]:
            dep_plugin = _integration_registry.get(module)
            plugin_requires = getattr(dep_plugin, "requires", None) if dep_plugin is not None else None
            deps: tuple[str, ...] = plugin_requires if plugin_requires else CONTRIB_DEPENDENCIES.get(module, ())
            for dep in deps:
                modules[dep] = True

    # Arguments take precedence over the environment and the defaults.
    modules.update(patch_modules)

    patch(raise_errors=False, **modules)


def patch(raise_errors: bool = True, **patch_modules: Union[list[str], bool]) -> None:
    """Patch only a set of given modules.

    :param bool raise_errors: Raise error if one patch fail.
    :param dict patch_modules: List of modules to patch.

        >>> patch(psycopg=True, elasticsearch=True)
    """
    contribs = {c: patch_indicator for c, patch_indicator in patch_modules.items() if patch_indicator}
    for contrib, patch_indicator in contribs.items():
        # Migrated plugins have an enable() method that does the necessary work (instrumentation
        # via patching etc.) -- routed through _integration_registry.enable_plugin(), never called
        # directly, so enable() only actually runs if the installed version is compatible with the
        # plugin's own supported_versions (see ddtrace/internal/integrations.py).
        plugin = _integration_registry.get(contrib)
        if plugin is not None:
            _integration_registry.enable_plugin(plugin)
            _PATCHED_MODULES.add(contrib)
            continue

        # Check if we have the requested contrib.
        base_path = Path(__file__).parent / "contrib" / "internal" / contrib
        if raise_errors and not (base_path / "patch.py").exists() and not (base_path / "patch.pyc").exists():
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


def _get_patched_modules() -> set[str]:
    """Get the list of patched modules"""
    return _PATCHED_MODULES
