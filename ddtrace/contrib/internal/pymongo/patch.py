import pymongo

from ddtrace import config
from ddtrace.internal import integration_hooks
from ddtrace.internal.database import DatabaseQueryAdapter
from ddtrace.internal.database import configure_database
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.utils.formats import asbool

from ....internal.schema import schematize_service_name
from .client import patch_pymongo_sync_modules
from .client import unpatch_pymongo_sync_modules
from .utils import dbm_comment_injector


log = get_logger(__name__)


config._add(
    "pymongo",
    dict(
        _default_service=schematize_service_name("pymongo"),
        _query_adapter=DatabaseQueryAdapter(2, "spec", dbm_comment_injector, "metadata"),
        _mongodb_obfuscation=_get_config("DD_TRACE_MONGODB_OBFUSCATION", default=True, modifier=asbool),
    ),
)


def get_version() -> str:
    return getattr(pymongo, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"pymongo": ">=3.8.0"}


def patch():
    """Patch pymongo synchronous and asynchronous modules."""
    if getattr(pymongo, "_datadog_patch", False):
        return
    patch_pymongo_sync_modules()
    if pymongo.version_tuple >= (4, 12):
        from .async_client import patch_pymongo_async_modules

        patch_pymongo_async_modules()
    pymongo._datadog_patch = True
    integration_hooks.on_patch("pymongo")
    configure_database(config.pymongo)


def unpatch():
    """Unpatch pymongo synchronous and asynchronous modules."""
    if not getattr(pymongo, "_datadog_patch", False):
        return
    unpatch_pymongo_sync_modules()
    if pymongo.version_tuple >= (4, 12):
        from .async_client import unpatch_pymongo_async_modules

        unpatch_pymongo_async_modules()
    pymongo._datadog_patch = False
    integration_hooks.on_unpatch("pymongo")
